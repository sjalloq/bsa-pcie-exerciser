#
# USB TLP Monitor - Packet Layouts and Constants
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Defines TLP type encodings and header layouts for the USB monitor subsystem.
# These must match the host-side protocol.py definitions.
#

from migen import *

# =============================================================================
# TLP Type Encoding
# =============================================================================

TLP_TYPE_MRD      = 0x0   # Memory Read Request
TLP_TYPE_MWR      = 0x1   # Memory Write Request
TLP_TYPE_CPL      = 0x2   # Completion (no data)
TLP_TYPE_CPLD     = 0x3   # Completion with Data
TLP_TYPE_MSIX     = 0x4   # MSI-X Write (TX only)
TLP_TYPE_ATS_REQ  = 0x5   # ATS Translation Request
TLP_TYPE_ATS_CPL  = 0x6   # ATS Translation Completion
TLP_TYPE_ATS_INV  = 0x7   # ATS Invalidation Message
TLP_TYPE_UNKNOWN  = 0xF   # Unknown/other

# Direction encoding
DIR_RX = 0  # Inbound (host -> device)
DIR_TX = 1  # Outbound (device -> host)

# =============================================================================
# Header Word Layouts (4 x 64-bit = 32 bytes)
# =============================================================================
#
# The capture engine writes 4 header words to the header FIFO, followed by
# variable-length payload to the payload FIFO.
#
# Word 0 (Header):
#     [9:0]   : payload_length (DWs from TLP header; 0 for no-payload TLPs)
#     [13:10] : tlp_type
#     [14]    : direction (0=RX, 1=TX)
#     [15]    : truncated (1 if payload was truncated due to FIFO backpressure)
#     [31:16] : header_word_count (fixed: 4)
#     [63:32] : timestamp[31:0]
#
# Word 1 (Header):
#     [31:0]  : timestamp[63:32]
#     [47:32] : req_id
#     [55:48] : tag
#     [59:56] : first_be
#     [63:60] : last_be
#
# Word 2 (Header):
#     [63:0]  : address
#
# Word 3 (Header):
#     [0]     : we (write enable)
#     [3:1]   : bar_hit (RX) / reserved (TX)
#     [5:4]   : attr
#     [7:6]   : at
#     [8]     : pasid_valid (TX) / reserved (RX)
#     [28:9]  : pasid (TX) / reserved (RX)
#     [29]    : privileged (TX requests) / status[0] (completions)
#     [30]    : execute (TX requests) / status[1] (completions)
#     [31]    : status[2] (completions) / reserved (TX requests)
#     [47:32] : cmp_id (completions)
#     [59:48] : byte_count (completions)
#     [63:60] : reserved
#
# Payload (Word 4+):
#     [63:0]  : TLP data beats (variable count based on payload_length)
#

HEADER_WORDS = 4
HEADER_WORD_COUNT = 4  # Encoded in header word 0


def build_header_word0(payload_length, tlp_type, direction, timestamp_lo, truncated=None):
    """
    Build header word 0.

    Args:
        payload_length: Actual payload length in DWs (10 bits)
        tlp_type: TLP type encoding (4 bits)
        direction: 0=RX, 1=TX (1 bit)
        timestamp_lo: Lower 32 bits of timestamp
        truncated: 1 if payload was truncated (1 bit), default 0

    Returns:
        64-bit header word 0
    """
    if truncated is None:
        truncated = Constant(0, 1)
    return Cat(
        payload_length[:10],           # [9:0]
        tlp_type[:4],                  # [13:10]
        direction,                     # [14]
        truncated,                     # [15] truncated flag
        Constant(HEADER_WORD_COUNT, 16),  # [31:16]
        timestamp_lo,                  # [63:32]
    )


def build_header_word1(timestamp_hi, req_id, tag, first_be, last_be):
    """
    Build header word 1.

    Args:
        timestamp_hi: Upper 32 bits of timestamp
        req_id: Requester ID (16 bits)
        tag: Transaction tag (8 bits)
        first_be: First DW byte enable (4 bits)
        last_be: Last DW byte enable (4 bits)

    Returns:
        64-bit header word 1
    """
    return Cat(
        timestamp_hi,       # [31:0]
        req_id[:16],        # [47:32]
        tag[:8],            # [55:48]
        first_be[:4],       # [59:56]
        last_be[:4],        # [63:60]
    )


def build_header_word2(address):
    """
    Build header word 2.

    Args:
        address: 64-bit address

    Returns:
        64-bit header word 2
    """
    return address[:64]


def build_header_word3_rx(we, bar_hit, attr, at, status, cmp_id, byte_count):
    """
    Build header word 3 for RX (inbound) TLPs.

    Args:
        we: Write enable (1 bit)
        bar_hit: BAR hit (3 bits)
        attr: Attributes (2 bits)
        at: Address type (2 bits)
        status: Completion status (3 bits)
        cmp_id: Completer ID (16 bits)
        byte_count: Byte count (12 bits)

    Returns:
        64-bit header word 3
    """
    return Cat(
        we,                     # [0]
        bar_hit[:3],            # [3:1]
        attr[:2],               # [5:4]
        at[:2],                 # [7:6]
        Constant(0, 1),         # [8] reserved (pasid_valid for TX)
        Constant(0, 20),        # [28:9] reserved (pasid for TX)
        status[:3],             # [31:29]
        cmp_id[:16],            # [47:32]
        byte_count[:12],        # [59:48]
        Constant(0, 4),         # [63:60] reserved
    )


def build_header_word3_tx(we, attr, at, pasid_valid, pasid, privileged, execute,
                          status, cmp_id, byte_count):
    """
    Build header word 3 for TX (outbound) TLPs.

    Args:
        we: Write enable (1 bit)
        attr: Attributes (2 bits)
        at: Address type (2 bits)
        pasid_valid: PASID present (1 bit)
        pasid: PASID value (20 bits)
        privileged: Privileged Mode Requested - PMR (1 bit, for requests)
        execute: Execute Requested - ER (1 bit, for requests)
        status: Completion status (3 bits, for completions)
        cmp_id: Completer ID (16 bits)
        byte_count: Byte count (12 bits)

    Returns:
        64-bit header word 3

    Note: For requests, bits [30:29] encode privileged/execute.
          For completions, bits [31:29] encode status (priv/exec are 0).
    """
    return Cat(
        we,                     # [0]
        Constant(0, 3),         # [3:1] reserved (bar_hit for RX)
        attr[:2],               # [5:4]
        at[:2],                 # [7:6]
        pasid_valid,            # [8]
        pasid[:20],             # [28:9]
        privileged,             # [29] PMR for requests, status[0] for completions
        execute,                # [30] ER for requests, status[1] for completions
        status[2],              # [31] status[2] for completions
        cmp_id[:16],            # [47:32]
        byte_count[:12],        # [59:48]
        Constant(0, 4),         # [63:60] reserved
    )
