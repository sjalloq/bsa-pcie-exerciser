#
# BSA PCIe Exerciser - Common Protocol Definitions
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Defines the packet format for USB monitor streaming.
# These definitions are shared between gateware and host tools.
#
# IMPORTANT: This module must have NO gateware dependencies (no migen/litex).
# The gateware layouts.py imports constants from here and adds Migen-specific
# helper functions.
#

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional, List


# =============================================================================
# TLP Type Constants
# =============================================================================
# These must match the gateware encoding in usb/monitor/layouts.py

TLP_TYPE_MRD      = 0x0   # Memory Read Request
TLP_TYPE_MWR      = 0x1   # Memory Write Request
TLP_TYPE_CPL      = 0x2   # Completion (no data)
TLP_TYPE_CPLD     = 0x3   # Completion with Data
TLP_TYPE_MSIX     = 0x4   # MSI-X Write (TX only)
TLP_TYPE_ATS_REQ  = 0x5   # ATS Translation Request
TLP_TYPE_ATS_CPL  = 0x6   # ATS Translation Completion
TLP_TYPE_ATS_INV  = 0x7   # ATS Invalidation Message
TLP_TYPE_UNKNOWN  = 0xF   # Unknown/other


# =============================================================================
# Direction Constants
# =============================================================================

DIR_RX = 0  # Inbound (host -> device)
DIR_TX = 1  # Outbound (device -> host)


# =============================================================================
# USB Framing Constants
# =============================================================================

USB_PREAMBLE = 0x5AA55AA5
USB_MONITOR_CHANNEL = 1
USB_FRAME_HEADER_SIZE = 12  # preamble + channel + length


# =============================================================================
# TLP Header Constants
# =============================================================================

TLP_HEADER_SIZE = 32   # 4 x 64-bit words = 8 x 32-bit words = 32 bytes
TLP_HEADER_WORDS = 4   # 64-bit words


# =============================================================================
# Enums
# =============================================================================

class TLPType(IntEnum):
    """TLP type codes from captured packets."""
    MRD      = TLP_TYPE_MRD
    MWR      = TLP_TYPE_MWR
    CPL      = TLP_TYPE_CPL
    CPLD     = TLP_TYPE_CPLD
    MSIX     = TLP_TYPE_MSIX
    ATS_REQ  = TLP_TYPE_ATS_REQ
    ATS_CPL  = TLP_TYPE_ATS_CPL
    ATS_INV  = TLP_TYPE_ATS_INV

    @classmethod
    def name(cls, value: int) -> str:
        """Get human-readable name for TLP type."""
        names = {
            cls.MRD: "MRd",
            cls.MWR: "MWr",
            cls.CPL: "Cpl",
            cls.CPLD: "CplD",
            cls.MSIX: "MSI-X",
            cls.ATS_REQ: "ATS Req",
            cls.ATS_CPL: "ATS Cpl",
            cls.ATS_INV: "ATS Inv",
        }
        try:
            return names.get(cls(value), f"Unknown({value})")
        except ValueError:
            return f"Unknown({value})"


class Direction(IntEnum):
    """Transaction direction."""
    RX = DIR_RX  # Inbound: Host -> Device
    TX = DIR_TX  # Outbound: Device -> Host


# =============================================================================
# Header Layout Documentation
# =============================================================================
#
# The USB monitor captures TLPs and streams them with a 4-word (32-byte) header
# followed by variable-length payload.
#
# Word 0 (64-bit):
#     [9:0]   : payload_length (DW count)
#     [13:10] : tlp_type
#     [14]    : direction (0=RX, 1=TX)
#     [15]    : reserved
#     [31:16] : header_word_count (always 4)
#     [63:32] : timestamp[31:0]
#
# Word 1 (64-bit):
#     [31:0]  : timestamp[63:32]
#     [47:32] : req_id
#     [55:48] : tag
#     [59:56] : first_be
#     [63:60] : last_be
#
# Word 2 (64-bit):
#     [63:0]  : address
#
# Word 3 (64-bit):
#     [0]     : we (write enable)
#     [3:1]   : bar_hit (RX) / reserved (TX)
#     [5:4]   : attr
#     [7:6]   : at
#     [8]     : pasid_valid (TX) / reserved (RX)
#     [28:9]  : pasid (TX) / reserved (RX)
#     [31:29] : status (completions)
#     [47:32] : cmp_id (completions)
#     [59:48] : byte_count (completions)
#     [63:60] : reserved
#
# Payload (Word 4+):
#     Variable-length TLP data (payload_length DWs)
#


# =============================================================================
# Data Structures
# =============================================================================

@dataclass
class TLPPacket:
    """
    Parsed TLP packet from USB monitor stream.

    See header layout documentation above for field positions.
    """
    # From header word 0
    payload_length: int   # DW count
    tlp_type: int
    direction: int
    header_words: int
    timestamp: int        # 64-bit timestamp

    # From header word 1
    req_id: int
    tag: int
    first_be: int
    last_be: int

    # From header word 2
    address: int

    # From header word 3
    we: bool              # Write enable
    bar_hit: int          # BAR hit (RX only)
    attr: int
    at: int
    pasid_valid: bool
    pasid: int
    status: int           # Completion status
    cmp_id: int           # Completer ID
    byte_count: int       # Completion byte count

    # Payload data (list of 32-bit words)
    payload: List[int]

    @property
    def type_name(self) -> str:
        """Human-readable TLP type name."""
        return TLPType.name(self.tlp_type)

    @property
    def is_completion(self) -> bool:
        """True if this is a completion TLP."""
        return self.tlp_type in (TLPType.CPL, TLPType.CPLD, TLPType.ATS_CPL)

    @property
    def is_write(self) -> bool:
        """True if this is a write request."""
        return self.tlp_type in (TLPType.MWR, TLPType.MSIX)

    @property
    def is_read(self) -> bool:
        """True if this is a read request."""
        return self.tlp_type == TLPType.MRD

    @property
    def direction_str(self) -> str:
        """Direction as arrow string."""
        return "<-" if self.direction == Direction.RX else "->"

    @property
    def timestamp_us(self) -> float:
        """Timestamp in microseconds (assuming 125MHz sys_clk = 8ns per tick)."""
        return self.timestamp * 0.008

    @property
    def timestamp_ms(self) -> float:
        """Timestamp in milliseconds."""
        return self.timestamp_us / 1000.0

    @property
    def payload_bytes(self) -> bytes:
        """Payload as raw bytes."""
        return b''.join(struct.pack('<I', w) for w in self.payload)

    def __str__(self) -> str:
        """Human-readable packet summary."""
        base = (
            f"{self.timestamp_us:12.3f}us "
            f"{self.direction_str} {self.type_name:8s} "
            f"addr=0x{self.address:016x} len={self.payload_length}"
        )
        if self.is_completion:
            return f"{base} status={self.status} cmp_id={self.cmp_id:04x}"
        else:
            return f"{base} tag={self.tag:02x} be={self.first_be:x}/{self.last_be:x}"


# =============================================================================
# Parsing Functions
# =============================================================================

def parse_usb_frame_header(data: bytes) -> Optional[tuple[int, int, int]]:
    """
    Parse USB frame header.

    Args:
        data: Raw USB data (at least 12 bytes)

    Returns:
        Tuple of (preamble, channel, length) or None if invalid
    """
    if len(data) < USB_FRAME_HEADER_SIZE:
        return None

    preamble, channel, length = struct.unpack('<III', data[:12])
    return preamble, channel, length


def parse_tlp_header(data: bytes) -> Optional[dict]:
    """
    Parse TLP header from 32 bytes (8 x 32-bit words).

    Args:
        data: Raw header data (at least 32 bytes)

    Returns:
        Dict with parsed header fields or None if invalid
    """
    if len(data) < TLP_HEADER_SIZE:
        return None

    # Parse as 8 x 32-bit words, then combine to 4 x 64-bit
    words32 = struct.unpack('<8I', data[:TLP_HEADER_SIZE])
    h0 = words32[0] | (words32[1] << 32)
    h1 = words32[2] | (words32[3] << 32)
    h2 = words32[4] | (words32[5] << 32)
    h3 = words32[6] | (words32[7] << 32)

    # Parse header word 0
    payload_length = h0 & 0x3FF              # [9:0]
    tlp_type = (h0 >> 10) & 0xF              # [13:10]
    direction = (h0 >> 14) & 0x1             # [14]
    header_words = (h0 >> 16) & 0xFFFF       # [31:16]
    timestamp_lo = (h0 >> 32) & 0xFFFFFFFF   # [63:32]

    # Parse header word 1
    timestamp_hi = h1 & 0xFFFFFFFF           # [31:0]
    req_id = (h1 >> 32) & 0xFFFF             # [47:32]
    tag = (h1 >> 48) & 0xFF                  # [55:48]
    first_be = (h1 >> 56) & 0xF              # [59:56]
    last_be = (h1 >> 60) & 0xF               # [63:60]

    # Combine timestamp
    timestamp = timestamp_lo | (timestamp_hi << 32)

    # Parse header word 2 (address)
    address = h2

    # Parse header word 3
    we = bool(h3 & 0x1)                      # [0]
    bar_hit = (h3 >> 1) & 0x7                # [3:1]
    attr = (h3 >> 4) & 0x3                   # [5:4]
    at = (h3 >> 6) & 0x3                     # [7:6]
    pasid_valid = bool((h3 >> 8) & 0x1)      # [8]
    pasid = (h3 >> 9) & 0xFFFFF              # [28:9]
    status = (h3 >> 29) & 0x7                # [31:29]
    cmp_id = (h3 >> 32) & 0xFFFF             # [47:32]
    byte_count = (h3 >> 48) & 0xFFF          # [59:48]

    return {
        'payload_length': payload_length,
        'tlp_type': tlp_type,
        'direction': direction,
        'header_words': header_words,
        'timestamp': timestamp,
        'address': address,
        'req_id': req_id,
        'tag': tag,
        'first_be': first_be,
        'last_be': last_be,
        'we': we,
        'bar_hit': bar_hit,
        'attr': attr,
        'at': at,
        'pasid': pasid,
        'pasid_valid': pasid_valid,
        'status': status,
        'byte_count': byte_count,
        'cmp_id': cmp_id,
    }


def parse_tlp_packet(data: bytes) -> Optional[TLPPacket]:
    """
    Parse a complete TLP packet (header + payload) from USB stream.

    The data should start at the TLP header (after USB frame header).

    Args:
        data: Raw packet data starting at TLP header

    Returns:
        TLPPacket if valid, None if invalid
    """
    header = parse_tlp_header(data)
    if header is None:
        return None

    # Calculate payload size (payload_length is in DWs, each DW = 4 bytes)
    payload_words = header['payload_length']
    payload_bytes_needed = payload_words * 4

    # Check if we have enough data for payload
    total_size = TLP_HEADER_SIZE + payload_bytes_needed
    if len(data) < total_size:
        return None

    # Parse payload as 32-bit words
    payload = []
    for i in range(payload_words):
        offset = TLP_HEADER_SIZE + i * 4
        word = struct.unpack('<I', data[offset:offset+4])[0]
        payload.append(word)

    return TLPPacket(
        payload_length=header['payload_length'],
        tlp_type=header['tlp_type'],
        direction=header['direction'],
        header_words=header['header_words'],
        timestamp=header['timestamp'],
        address=header['address'],
        req_id=header['req_id'],
        tag=header['tag'],
        first_be=header['first_be'],
        last_be=header['last_be'],
        we=header['we'],
        bar_hit=header['bar_hit'],
        attr=header['attr'],
        at=header['at'],
        pasid=header['pasid'],
        pasid_valid=header['pasid_valid'],
        status=header['status'],
        byte_count=header['byte_count'],
        cmp_id=header['cmp_id'],
        payload=payload,
    )


def find_usb_frame(data: bytes, offset: int = 0) -> tuple[Optional[bytes], int]:
    """
    Find next valid USB frame in a byte stream.

    Searches for the preamble and extracts the frame payload.

    Args:
        data: Byte stream to search
        offset: Starting offset

    Returns:
        Tuple of (frame_payload, consumed_bytes) where frame_payload is
        the TLP data (header + payload) or None if not found
    """
    preamble_bytes = struct.pack('<I', USB_PREAMBLE)

    while offset <= len(data) - USB_FRAME_HEADER_SIZE:
        # Search for preamble
        idx = data.find(preamble_bytes, offset)
        if idx < 0:
            return None, len(data)

        # Check if we have enough data for USB header
        if idx + USB_FRAME_HEADER_SIZE > len(data):
            return None, idx

        # Parse USB header
        frame_header = parse_usb_frame_header(data[idx:])
        if frame_header is None:
            offset = idx + 1
            continue

        preamble, channel, length = frame_header

        # Verify channel
        if channel != USB_MONITOR_CHANNEL:
            offset = idx + 1
            continue

        # Check if we have complete frame
        frame_end = idx + USB_FRAME_HEADER_SIZE + length
        if frame_end > len(data):
            return None, idx  # Need more data

        # Extract frame payload
        payload = data[idx + USB_FRAME_HEADER_SIZE:frame_end]
        return payload, frame_end

    return None, len(data)


def parse_stream(data: bytes) -> List[TLPPacket]:
    """
    Parse a stream of USB monitor data into TLP packets.

    Args:
        data: Raw USB stream data

    Returns:
        List of parsed TLP packets
    """
    packets = []
    offset = 0

    while offset < len(data):
        # Find next USB frame
        frame_payload, consumed = find_usb_frame(data, offset)
        if frame_payload is None:
            break

        offset = consumed

        # Parse TLP packet from frame
        packet = parse_tlp_packet(frame_payload)
        if packet:
            packets.append(packet)

    return packets


def packet_to_dict(pkt: TLPPacket) -> dict:
    """Convert packet to dictionary for JSON export."""
    result = {
        'timestamp_ticks': pkt.timestamp,
        'timestamp_us': pkt.timestamp_us,
        'type': pkt.type_name,
        'type_code': pkt.tlp_type,
        'direction': 'rx' if pkt.direction == Direction.RX else 'tx',
        'address': f"0x{pkt.address:016x}",
        'req_id': f"0x{pkt.req_id:04x}",
        'tag': pkt.tag,
        'payload_length': pkt.payload_length,
    }

    if pkt.is_completion:
        result['status'] = pkt.status
        result['byte_count'] = pkt.byte_count
        result['cmp_id'] = f"0x{pkt.cmp_id:04x}"
    else:
        result['first_be'] = pkt.first_be
        result['last_be'] = pkt.last_be
        result['attr'] = pkt.attr
        result['at'] = pkt.at
        result['we'] = pkt.we
        if pkt.direction == Direction.RX:
            result['bar_hit'] = pkt.bar_hit
        if pkt.pasid_valid:
            result['pasid'] = pkt.pasid

    if pkt.payload:
        result['payload'] = [f"0x{w:08x}" for w in pkt.payload]

    return result


# =============================================================================
# Legacy Compatibility
# =============================================================================

# Aliases for backwards compatibility with old bsa_monitor code
MonitorPacket = TLPPacket
PacketType = TLPType
MONITOR_MAGIC = USB_PREAMBLE
HEADER_SIZE = TLP_HEADER_SIZE
PACKET_SIZE = TLP_HEADER_SIZE

def parse_packet(data: bytes) -> Optional[TLPPacket]:
    """Legacy compatibility wrapper."""
    return parse_tlp_packet(data)
