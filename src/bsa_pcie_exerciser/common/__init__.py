#
# BSA PCIe Exerciser - Common Definitions
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Shared protocol definitions used by both gateware and host tools.
# This module has no gateware dependencies (no migen/litex).
#

from .protocol import (
    # TLP Type constants
    TLP_TYPE_MRD,
    TLP_TYPE_MWR,
    TLP_TYPE_CPL,
    TLP_TYPE_CPLD,
    TLP_TYPE_MSIX,
    TLP_TYPE_ATS_REQ,
    TLP_TYPE_ATS_CPL,
    TLP_TYPE_ATS_INV,
    TLP_TYPE_UNKNOWN,
    # Direction constants
    DIR_RX,
    DIR_TX,
    # Header constants
    TLP_HEADER_SIZE,
    TLP_HEADER_WORDS,
    # Enums
    TLPType,
    Direction,
    # Data structures
    TLPPacket,
    # Parsing functions
    parse_tlp_header,
    parse_tlp_packet,
    parse_usb_frame_header,
    find_usb_frame,
    parse_stream,
    packet_to_dict,
    # USB framing constants
    USB_PREAMBLE,
    USB_MONITOR_CHANNEL,
    USB_FRAME_HEADER_SIZE,
)

__all__ = [
    # TLP Type constants
    "TLP_TYPE_MRD",
    "TLP_TYPE_MWR",
    "TLP_TYPE_CPL",
    "TLP_TYPE_CPLD",
    "TLP_TYPE_MSIX",
    "TLP_TYPE_ATS_REQ",
    "TLP_TYPE_ATS_CPL",
    "TLP_TYPE_ATS_INV",
    "TLP_TYPE_UNKNOWN",
    # Direction constants
    "DIR_RX",
    "DIR_TX",
    # Header constants
    "TLP_HEADER_SIZE",
    "TLP_HEADER_WORDS",
    # Enums
    "TLPType",
    "Direction",
    # Data structures
    "TLPPacket",
    # Parsing functions
    "parse_tlp_header",
    "parse_tlp_packet",
    "parse_usb_frame_header",
    "find_usb_frame",
    "parse_stream",
    "packet_to_dict",
    # USB framing constants
    "USB_PREAMBLE",
    "USB_MONITOR_CHANNEL",
    "USB_FRAME_HEADER_SIZE",
]
