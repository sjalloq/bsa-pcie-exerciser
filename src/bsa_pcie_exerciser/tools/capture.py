#
# BSA Monitor - UDP Capture and Decode
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Captures monitor packets from UDP and decodes them.
#

import socket
import threading
import time
from collections import deque
from typing import Callable, Optional

from bsa_pcie_exerciser.common.protocol import (
    TLPPacket, find_usb_frame, parse_tlp_packet,
    TLP_HEADER_SIZE, USB_FRAME_HEADER_SIZE,
)

# Alias for backwards compatibility
MonitorPacket = TLPPacket
PACKET_SIZE = TLP_HEADER_SIZE


def find_packet(data: bytes) -> tuple:
    """Find and parse next packet from data stream."""
    frame, consumed = find_usb_frame(data)
    if frame is None:
        return None, consumed
    packet = parse_tlp_packet(frame)
    return packet, consumed

# Default UDP port for monitor stream (must match usb2udp)
DEFAULT_MONITOR_PORT = 2345


class MonitorCapture:
    """
    Captures monitor packets from UDP.

    The usb2udp daemon bridges USB channel 1 to UDP port 2345.
    This class receives those packets and decodes them.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_MONITOR_PORT,
        callback: Optional[Callable[[MonitorPacket], None]] = None,
        buffer_size: int = 1000,
    ):
        """
        Initialize monitor capture.

        Args:
            host: UDP host to bind to
            port: UDP port to listen on
            callback: Optional callback for each received packet
            buffer_size: Max packets to buffer (oldest dropped when full)
        """
        self.host = host
        self.port = port
        self.callback = callback
        self.buffer_size = buffer_size

        self._socket: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._packets: deque[MonitorPacket] = deque(maxlen=buffer_size)
        self._lock = threading.Lock()

        # Statistics
        self.stats = {
            'packets_received': 0,
            'bytes_received': 0,
            'parse_errors': 0,
            'start_time': 0.0,
        }

    def start(self) -> None:
        """Start capturing packets."""
        if self._running:
            return

        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._socket.bind((self.host, self.port))
        self._socket.settimeout(0.5)  # Allow periodic checks for stop

        self._running = True
        self.stats['start_time'] = time.time()
        self._thread = threading.Thread(target=self._receive_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop capturing packets."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._socket:
            self._socket.close()
            self._socket = None

    def _receive_loop(self) -> None:
        """Background thread to receive UDP packets."""
        buffer = b''

        while self._running:
            try:
                data, addr = self._socket.recvfrom(65536)
                self.stats['bytes_received'] += len(data)
                buffer += data

                # Extract all complete packets from buffer
                while len(buffer) >= PACKET_SIZE:
                    packet, consumed = find_packet(buffer)
                    if packet is None:
                        # No valid packet found, discard some data to prevent
                        # buffer from growing indefinitely
                        buffer = buffer[max(1, consumed - PACKET_SIZE):]
                        self.stats['parse_errors'] += 1
                        break

                    buffer = buffer[consumed:]
                    self.stats['packets_received'] += 1

                    with self._lock:
                        self._packets.append(packet)

                    if self.callback:
                        try:
                            self.callback(packet)
                        except Exception:
                            pass  # Don't let callback errors stop capture

            except socket.timeout:
                continue
            except OSError:
                break

    def get_packets(self, clear: bool = False) -> list[MonitorPacket]:
        """
        Get buffered packets.

        Args:
            clear: If True, clear the buffer after returning

        Returns:
            List of packets in buffer
        """
        with self._lock:
            packets = list(self._packets)
            if clear:
                self._packets.clear()
        return packets

    def get_packet(self, timeout: float = 1.0) -> Optional[MonitorPacket]:
        """
        Get next packet from buffer.

        Args:
            timeout: Max time to wait for packet

        Returns:
            Next packet or None if timeout
        """
        start = time.time()
        while time.time() - start < timeout:
            with self._lock:
                if self._packets:
                    return self._packets.popleft()
            time.sleep(0.01)
        return None

    @property
    def running(self) -> bool:
        """Check if capture is running."""
        return self._running

    @property
    def packet_rate(self) -> float:
        """Calculate packets per second."""
        elapsed = time.time() - self.stats['start_time']
        if elapsed > 0:
            return self.stats['packets_received'] / elapsed
        return 0.0


class FileCapture:
    """
    Writes captured packets to a binary file.

    File format:
      - 16 byte header: "BSACAP01" + 8 bytes reserved
      - Sequence of 48-byte packets
    """

    MAGIC = b'BSACAP01'
    HEADER_SIZE = 16

    def __init__(self, filename: str):
        """
        Initialize file capture.

        Args:
            filename: Output file path
        """
        self.filename = filename
        self._file = None
        self._count = 0

    def __enter__(self):
        self._file = open(self.filename, 'wb')
        # Write header
        header = self.MAGIC + b'\x00' * 8
        self._file.write(header)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._file:
            self._file.close()
            self._file = None

    def write(self, packet: MonitorPacket) -> None:
        """Write a packet to the file."""
        if self._file:
            # Reconstruct raw packet bytes
            import struct
            w4 = packet.bar_hit | (packet.direction << 8) | (packet.pkt_type << 16)
            attr_byte = packet.attr | (packet.at << 2)
            w5 = (attr_byte << 8) | (packet.last_be << 16) | (packet.first_be << 24)
            w6 = packet.req_id | (packet.length << 16)
            w7 = packet.tag << 24

            data = struct.pack(
                '<12I',
                packet.magic,
                packet.seq,
                packet.timestamp & 0xFFFFFFFF,
                packet.timestamp >> 32,
                w4, w5, w6, w7,
                packet.address & 0xFFFFFFFF,
                packet.address >> 32,
                packet.data & 0xFFFFFFFF,
                packet.data >> 32,
            )
            self._file.write(data)
            self._count += 1

    @property
    def count(self) -> int:
        """Number of packets written."""
        return self._count


def read_capture_file(filename: str):
    """
    Read packets from a capture file.

    Args:
        filename: Capture file path

    Yields:
        TLPPacket for each packet in file
    """
    from bsa_pcie_exerciser.common.protocol import parse_tlp_packet

    with open(filename, 'rb') as f:
        # Read and verify header
        header = f.read(FileCapture.HEADER_SIZE)
        if not header.startswith(FileCapture.MAGIC):
            raise ValueError(f"Invalid capture file: {filename}")

        # Read packets (note: old format may not be compatible)
        while True:
            data = f.read(PACKET_SIZE)
            if len(data) < PACKET_SIZE:
                break
            packet = parse_tlp_packet(data)
            if packet:
                yield packet
