#
# USB Bus Functional Model for Cocotb Testing
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# BFM for driving/monitoring the FT601 interface (via stub) from cocotb.
# Implements USB packet framing and Etherbone protocol.
#

import struct
from collections import deque
from typing import Optional

import cocotb
from cocotb.triggers import ClockCycles, RisingEdge, with_timeout, Event


# =============================================================================
# USB Stream Protocol Constants
# =============================================================================

USB_PREAMBLE = 0x5AA55AA5
USB_CHANNEL_ETHERBONE = 0
USB_CHANNEL_MONITOR = 1


# =============================================================================
# Etherbone Protocol Constants
# =============================================================================

ETHERBONE_MAGIC = 0x4e6f
ETHERBONE_VERSION = 1

# Etherbone packet header (8 bytes)
# Byte 0-1: magic (big-endian)
# Byte 2: version[7:4], reserved[3], nr[2], pr[1], pf[0]
# Byte 3: addr_size[7:4], port_size[3:0]
# Byte 4-7: reserved (padding to 8 bytes in some implementations)

# Etherbone record header (4 bytes)
# Byte 0: reserved[7], wff[6], wca[5], cyc[4], reserved[3], rff[2], rca[1], bca[0]
# Byte 1: byte_enable
# Byte 2: wcount
# Byte 3: rcount


class USBBFM:
    """
    USB Bus Functional Model for FT601 interface testing.

    Drives the FT601Stub signals exposed at testbench top level.
    Implements USB packet framing and Etherbone protocol.

    Capabilities:
    - Inject USB packets (Etherbone requests, etc.)
    - Capture USB packets (monitor stream, Etherbone responses)
    - Control backpressure
    - Etherbone CSR read/write operations
    """

    def __init__(self, dut, sys_clk_name="sys_clk"):
        """
        Args:
            dut: Testbench DUT with exposed USB signals
            sys_clk_name: Name of system clock signal
        """
        self.dut = dut
        self.clk = getattr(dut, sys_clk_name)

        # USB stub signals (exposed at top level)
        self.inject_valid = dut.usb_inject_valid
        self.inject_ready = dut.usb_inject_ready
        self.inject_data = dut.usb_inject_data

        self.capture_valid = dut.usb_capture_valid
        self.capture_ready = dut.usb_capture_ready
        self.capture_data = dut.usb_capture_data

        self.tx_backpressure = dut.usb_tx_backpressure

        # Initialize signals
        self.inject_valid.value = 0
        self.inject_data.value = 0
        self.capture_ready.value = 1  # Always ready - background task captures
        self.tx_backpressure.value = 0

        # Background capture queue and task
        self._capture_queue = deque()
        self._data_available = Event()
        # Buffer for non-Etherbone packets received during Etherbone operations
        self._pending_monitor_packets = deque()
        # Start background capture automatically
        self._capture_task = cocotb.start_soon(self._background_capture())

    async def _background_capture(self):
        """Background task that continuously captures data into queue."""
        while True:
            await RisingEdge(self.clk)
            # Only capture when transfer actually happens (valid && !backpressure)
            # Note: capture_ready is always 1, so effective ready = !backpressure
            valid = int(self.capture_valid.value)
            backpressure = int(self.tx_backpressure.value)
            if valid == 1 and backpressure == 0:
                data = int(self.capture_data.value)
                self._capture_queue.append(data)
                self._data_available.set()

    def _get_queued_word(self) -> Optional[int]:
        """Get a word from the capture queue, or None if empty."""
        if self._capture_queue:
            return self._capture_queue.popleft()
        return None

    # =========================================================================
    # Low-Level Packet Operations
    # =========================================================================

    async def _inject_word(self, word: int):
        """Inject a single 32-bit word using proper AXI-stream handshake."""
        self.inject_data.value = word
        self.inject_valid.value = 1

        # Wait until transfer completes (valid && ready at clock edge)
        while True:
            # Sample ready before the clock edge
            ready_before = int(self.inject_ready.value)
            await RisingEdge(self.clk)
            # Transfer happens if ready was high at the rising edge
            if ready_before == 1:
                break

        self.inject_valid.value = 0

    async def _capture_word(self, timeout_cycles: int = 1000) -> Optional[int]:
        """Get next word from capture queue, waiting if necessary."""
        for _ in range(timeout_cycles):
            word = self._get_queued_word()
            if word is not None:
                return word
            # Wait for data to arrive
            self._data_available.clear()
            await RisingEdge(self.clk)
        return None

    async def send_packet(self, channel: int, data: bytes):
        """
        Send a USB packet to the device.

        Args:
            channel: USB channel (0=Etherbone, 1=Monitor)
            data: Packet payload bytes (will be padded to 32-bit boundary)

        Frames data with USB stream protocol:
        - Preamble: 0x5AA55AA5
        - Channel: 32-bit
        - Length: 32-bit (payload length in bytes)
        - Payload: data bytes (padded to 32-bit boundary)
        """
        # Pad to 32-bit boundary
        padding = (4 - (len(data) % 4)) % 4
        padded_data = data + bytes(padding)

        # Send frame header
        await self._inject_word(USB_PREAMBLE)
        await self._inject_word(channel)
        await self._inject_word(len(data))  # Original length, not padded

        # Send payload words
        for i in range(0, len(padded_data), 4):
            word = struct.unpack('<I', padded_data[i:i+4])[0]
            await self._inject_word(word)

    async def receive_packet(self, timeout_cycles: int = 1000, debug: bool = False) -> Optional[tuple[int, bytes]]:
        """
        Receive a USB packet from the device.

        Args:
            timeout_cycles: Maximum cycles to wait for packet
            debug: If True, print debug info about first few words seen

        Returns:
            (channel, data) tuple, or None on timeout
        """
        from cocotb.utils import get_sim_time
        if debug:
            self.dut._log.info(f"[BFM] receive_packet called at {get_sim_time('ns')}ns, queue size={len(self._capture_queue)}")
        # Wait for preamble from capture queue
        preamble_found = False
        debug_count = 0
        for i in range(timeout_cycles):
            word = self._get_queued_word()
            if word is not None:
                if debug and debug_count < 5:
                    self.dut._log.info(f"receive_packet iter {i}: data=0x{word:08X}")
                    debug_count += 1
                if word == USB_PREAMBLE:
                    preamble_found = True
                    break
                # Not preamble - discard and continue looking
            else:
                # No data available, wait for next cycle
                self._data_available.clear()
                await RisingEdge(self.clk)

        if not preamble_found:
            if debug:
                self.dut._log.info(f"receive_packet: preamble not found after {timeout_cycles} cycles")
            return None

        # Read channel
        channel = await self._capture_word(timeout_cycles)
        if channel is None:
            return None

        # Read length
        length = await self._capture_word(timeout_cycles)
        if length is None:
            return None

        # Read payload words
        num_words = (length + 3) // 4
        payload = b''
        for _ in range(num_words):
            word = await self._capture_word(timeout_cycles)
            if word is None:
                return None
            payload += struct.pack('<I', word)

        # Trim to actual length
        return (channel, payload[:length])

    # =========================================================================
    # Etherbone Protocol Operations
    # =========================================================================

    def _build_etherbone_packet(self, pf: bool = False, pr: bool = False,
                                 nr: bool = False) -> bytes:
        """Build Etherbone packet header (8 bytes)."""
        # Magic (big-endian)
        header = struct.pack('>H', ETHERBONE_MAGIC)

        # Byte 2: version[7:4] | reserved[3] | nr[2] | pr[1] | pf[0]
        byte2 = (ETHERBONE_VERSION << 4) | (int(nr) << 2) | (int(pr) << 1) | int(pf)
        header += bytes([byte2])

        # Byte 3: addr_size[7:4] | port_size[3:0] (both 4 for 32-bit)
        header += bytes([0x44])

        # Padding (4 bytes to make header 8 bytes)
        header += bytes(4)

        return header

    def _build_etherbone_record(self, wcount: int, rcount: int,
                                 byte_enable: int = 0x0F, cyc: bool = True) -> bytes:
        """Build Etherbone record header (4 bytes)."""
        # Byte 0: flags (cyc=1)
        byte0 = (int(cyc) << 4)
        # Byte 1: byte_enable
        # Byte 2: wcount
        # Byte 3: rcount
        return bytes([byte0, byte_enable, wcount, rcount])

    async def send_etherbone_probe(self):
        """
        Send Etherbone probe request.

        Used to discover Etherbone endpoints.
        """
        packet = self._build_etherbone_packet(pf=True)
        await self.send_packet(USB_CHANNEL_ETHERBONE, packet)

    async def wait_etherbone_probe_response(self, timeout_cycles: int = 1000) -> bool:
        """
        Wait for Etherbone probe response.

        Returns:
            True if probe response received, False on timeout
        """
        result = await self.receive_packet(timeout_cycles)
        if result is None:
            return False
        channel, data = result
        if channel != USB_CHANNEL_ETHERBONE or len(data) < 4:
            return False
        # Check for probe response flag (pr=1)
        if len(data) >= 3:
            byte2 = data[2]
            pr = (byte2 >> 1) & 1
            return pr == 1
        return False

    async def send_etherbone_read(self, address: int, timeout_cycles: int = 2000) -> int:
        """
        Send Etherbone read request and wait for response.

        Args:
            address: CSR address to read (byte address)
            timeout_cycles: Maximum cycles to wait for response

        Returns:
            32-bit read data

        Raises:
            TimeoutError: If no response received
            ValueError: If response is malformed
        """
        # Build packet: header + record + base_addr + read_addr
        packet = self._build_etherbone_packet()
        packet += self._build_etherbone_record(wcount=0, rcount=1)

        # Base return address (where to write response) - not used, set to 0
        packet += struct.pack('>I', 0)

        # Read address (big-endian as per Etherbone spec)
        packet += struct.pack('>I', address)

        await self.send_packet(USB_CHANNEL_ETHERBONE, packet)

        # Wait for response, saving non-Etherbone packets for later retrieval
        max_other_packets = 1000
        other_count = 0
        while other_count < max_other_packets:
            result = await self.receive_packet(timeout_cycles)
            if result is None:
                raise TimeoutError(f"No Etherbone response for read at 0x{address:08X}")

            channel, data = result
            if channel == USB_CHANNEL_ETHERBONE:
                break
            # Save non-Etherbone packets (e.g., monitor traffic) for later retrieval
            if channel == USB_CHANNEL_MONITOR:
                self._pending_monitor_packets.append(data)
            other_count += 1
        else:
            raise TimeoutError(f"No Etherbone response for read at 0x{address:08X} (saw {other_count} other packets)")

        # Parse response: header (8) + record (4) + base_addr (4) + data (4)
        if len(data) < 20:
            raise ValueError(f"Etherbone response too short: {len(data)} bytes")

        # Read data is at offset 16 (after header + record + base_addr), big-endian
        read_data = struct.unpack('>I', data[16:20])[0]
        return read_data

    async def send_etherbone_write(self, address: int, data: int,
                                    timeout_cycles: int = 1000):
        """
        Send Etherbone write request.

        Args:
            address: CSR address to write (byte address)
            data: 32-bit value to write
            timeout_cycles: Cycles to wait after sending (for write to complete)
        """
        # Build packet: header + record + base_addr + data
        packet = self._build_etherbone_packet()
        packet += self._build_etherbone_record(wcount=1, rcount=0)

        # Base address (big-endian)
        packet += struct.pack('>I', address)

        # Write data (big-endian)
        packet += struct.pack('>I', data)

        await self.send_packet(USB_CHANNEL_ETHERBONE, packet)

        # Allow some cycles for the write to propagate
        await ClockCycles(self.clk, 10)

    async def send_etherbone_burst_read(self, addresses: list[int],
                                         timeout_cycles: int = 5000) -> list[int]:
        """
        Burst read multiple addresses via single Etherbone packet.

        Args:
            addresses: List of CSR addresses to read
            timeout_cycles: Maximum cycles to wait for response

        Returns:
            List of 32-bit read values
        """
        if not addresses:
            return []

        # Build packet: header + record + base_addr + read_addrs...
        packet = self._build_etherbone_packet()
        packet += self._build_etherbone_record(wcount=0, rcount=len(addresses))

        # Base return address (not used)
        packet += struct.pack('>I', 0)

        # Read addresses (big-endian)
        for addr in addresses:
            packet += struct.pack('>I', addr)

        await self.send_packet(USB_CHANNEL_ETHERBONE, packet)

        # Wait for response, saving non-Etherbone packets for later retrieval
        max_other_packets = 1000
        other_count = 0
        while other_count < max_other_packets:
            result = await self.receive_packet(timeout_cycles)
            if result is None:
                raise TimeoutError("No Etherbone response for burst read")

            channel, data = result
            if channel == USB_CHANNEL_ETHERBONE:
                break
            # Save non-Etherbone packets (e.g., monitor traffic) for later retrieval
            if channel == USB_CHANNEL_MONITOR:
                self._pending_monitor_packets.append(data)
            other_count += 1
        else:
            raise TimeoutError(f"No Etherbone response for burst read (saw {other_count} other packets)")

        # Parse response: header (8) + record (4) + base_addr (4) + data (4*n)
        expected_len = 16 + 4 * len(addresses)
        if len(data) < expected_len:
            raise ValueError(f"Response too short: {len(data)} < {expected_len}")

        # Extract read data values
        values = []
        for i in range(len(addresses)):
            offset = 16 + i * 4
            val = struct.unpack('>I', data[offset:offset+4])[0]
            values.append(val)

        return values

    async def send_etherbone_burst_write(self, base_address: int, values: list[int],
                                          timeout_cycles: int = 1000):
        """
        Burst write multiple consecutive addresses via single Etherbone packet.

        Args:
            base_address: Starting CSR address
            values: List of 32-bit values to write
            timeout_cycles: Cycles to wait after sending
        """
        if not values:
            return

        # Build packet: header + record + base_addr + data...
        packet = self._build_etherbone_packet()
        packet += self._build_etherbone_record(wcount=len(values), rcount=0)

        # Base address (big-endian)
        packet += struct.pack('>I', base_address)

        # Write data (big-endian)
        for val in values:
            packet += struct.pack('>I', val)

        await self.send_packet(USB_CHANNEL_ETHERBONE, packet)

        # Allow some cycles for the writes to propagate
        await ClockCycles(self.clk, 10 + len(values) * 2)

    # =========================================================================
    # Monitor Packet Operations
    # =========================================================================

    async def receive_monitor_packet(self, timeout_cycles: int = 1000, debug: bool = False) -> Optional[bytes]:
        """
        Receive a TLP monitor packet from USB channel 1.

        Returns:
            Raw monitor packet data (header + payload), or None on timeout
        """
        # First check for packets buffered during Etherbone operations
        if self._pending_monitor_packets:
            return self._pending_monitor_packets.popleft()

        result = await self.receive_packet(timeout_cycles, debug=debug)
        if result is None:
            return None

        channel, data = result
        if channel != USB_CHANNEL_MONITOR:
            if debug:
                self.dut._log.info(f"receive_monitor_packet: got channel {channel}, expected {USB_CHANNEL_MONITOR}")
            return None

        return data

    # =========================================================================
    # Backpressure Control
    # =========================================================================

    def set_backpressure(self, enabled: bool):
        """Enable/disable TX backpressure (device->host direction)."""
        self.tx_backpressure.value = int(enabled)

    def set_capture_ready(self, ready: bool):
        """Control whether BFM is ready to receive data."""
        self.capture_ready.value = int(ready)
