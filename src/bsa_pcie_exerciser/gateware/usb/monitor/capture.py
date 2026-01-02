#
# USB TLP Monitor - Capture Engine
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Captures TLPs from parsed tap points and streams to header/payload FIFOs.
# Parameterized for RX (inbound) or TX (outbound) direction.
#
# Design: Simple - push header to 256-bit FIFO on first beat,
# push payload to 64-bit FIFO every beat.
#

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream

from .layouts import (
    TLP_TYPE_MRD, TLP_TYPE_MWR, TLP_TYPE_CPL, TLP_TYPE_CPLD,
    TLP_TYPE_MSIX, TLP_TYPE_ATS_REQ, TLP_TYPE_ATS_CPL, TLP_TYPE_ATS_INV,
    DIR_RX, DIR_TX, HEADER_WORDS,
    build_header_word0, build_header_word1, build_header_word2,
    build_header_word3_rx, build_header_word3_tx,
)


class TLPCaptureEngine(LiteXModule):
    """
    Captures TLPs from tap points and streams to header/payload FIFOs.

    Simple design:
    - Header (256-bit) pushed to FIFO on first beat
    - Payload (64-bit) pushed to FIFO every beat
    - Drop packet if header FIFO full on first beat

    Parameters
    ----------
    data_width : int
        PCIe data width (64).

    direction : int
        0 = RX (inbound), 1 = TX (outbound).
        Affects header word 3 layout (bar_hit vs pasid).
    """

    def __init__(self, data_width=64, direction=DIR_RX):
        assert data_width == 64, "Only 64-bit data width supported"

        self.direction = direction

        # =====================================================================
        # Control Interface
        # =====================================================================

        self.enable = Signal()
        self.timestamp = Signal(64)
        self.clear_stats = Signal()

        # =====================================================================
        # Tap Interface - Request Source/Sink
        # =====================================================================

        self.tap_req_valid   = Signal()
        self.tap_req_ready   = Signal()
        self.tap_req_first   = Signal()
        self.tap_req_last    = Signal()
        self.tap_req_we      = Signal()
        self.tap_req_adr     = Signal(64)
        self.tap_req_len     = Signal(10)
        self.tap_req_req_id  = Signal(16)
        self.tap_req_tag     = Signal(8)
        self.tap_req_dat     = Signal(data_width)
        self.tap_req_first_be = Signal(4)
        self.tap_req_last_be  = Signal(4)
        self.tap_req_attr    = Signal(2)
        self.tap_req_at      = Signal(2)

        # RX only: BAR hit
        self.tap_req_bar_hit = Signal(3)

        # TX only: PASID
        self.tap_req_pasid_valid = Signal()
        self.tap_req_pasid   = Signal(20)

        # =====================================================================
        # Tap Interface - Completion Source/Sink
        # =====================================================================

        self.tap_cpl_valid   = Signal()
        self.tap_cpl_ready   = Signal()
        self.tap_cpl_first   = Signal()
        self.tap_cpl_last    = Signal()
        self.tap_cpl_adr     = Signal(64)
        self.tap_cpl_len     = Signal(10)
        self.tap_cpl_req_id  = Signal(16)
        self.tap_cpl_tag     = Signal(8)
        self.tap_cpl_dat     = Signal(data_width)
        self.tap_cpl_status  = Signal(3)
        self.tap_cpl_cmp_id  = Signal(16)
        self.tap_cpl_byte_count = Signal(12)

        # =====================================================================
        # Output Interfaces
        # =====================================================================

        # Header FIFO (256-bit stream, one entry per TLP)
        self.header_sink = stream.Endpoint([("data", 256)])

        # Payload FIFO (64-bit stream)
        self.payload_sink = stream.Endpoint([("data", 64)])

        # =====================================================================
        # Statistics
        # =====================================================================

        self.packets_captured = Signal(32)
        self.packets_dropped = Signal(32)

        # =====================================================================
        # Tap Signal Muxing
        # =====================================================================

        tap_valid = Signal()
        tap_first = Signal()
        tap_last = Signal()
        tap_we = Signal()
        tap_adr = Signal(64)
        tap_len = Signal(10)
        tap_req_id = Signal(16)
        tap_tag = Signal(8)
        tap_dat = Signal(data_width)
        tap_first_be = Signal(4)
        tap_last_be = Signal(4)
        tap_attr = Signal(2)
        tap_at = Signal(2)
        tap_bar_hit = Signal(3)
        tap_pasid_valid = Signal()
        tap_pasid = Signal(20)
        tap_status = Signal(3)
        tap_cmp_id = Signal(16)
        tap_byte_count = Signal(12)
        tap_is_request = Signal()
        tap_is_completion = Signal()

        # Request active when valid & ready
        req_active = Signal()
        cpl_active = Signal()
        self.comb += [
            req_active.eq(self.tap_req_valid & self.tap_req_ready),
            cpl_active.eq(self.tap_cpl_valid & self.tap_cpl_ready),
        ]

        # Mux: request takes priority
        self.comb += [
            tap_is_request.eq(req_active),
            tap_is_completion.eq(cpl_active & ~req_active),
            tap_valid.eq(req_active | cpl_active),

            If(req_active,
                tap_first.eq(self.tap_req_first),
                tap_last.eq(self.tap_req_last),
                tap_we.eq(self.tap_req_we),
                tap_adr.eq(self.tap_req_adr),
                tap_len.eq(self.tap_req_len),
                tap_req_id.eq(self.tap_req_req_id),
                tap_tag.eq(self.tap_req_tag),
                tap_dat.eq(self.tap_req_dat),
                tap_first_be.eq(self.tap_req_first_be),
                tap_last_be.eq(self.tap_req_last_be),
                tap_attr.eq(self.tap_req_attr),
                tap_at.eq(self.tap_req_at),
                tap_bar_hit.eq(self.tap_req_bar_hit),
                tap_pasid_valid.eq(self.tap_req_pasid_valid),
                tap_pasid.eq(self.tap_req_pasid),
                tap_status.eq(0),
                tap_cmp_id.eq(0),
                tap_byte_count.eq(0),
            ).Else(
                tap_first.eq(self.tap_cpl_first),
                tap_last.eq(self.tap_cpl_last),
                tap_we.eq(0),
                tap_adr.eq(self.tap_cpl_adr),
                tap_len.eq(self.tap_cpl_len),
                tap_req_id.eq(self.tap_cpl_req_id),
                tap_tag.eq(self.tap_cpl_tag),
                tap_dat.eq(self.tap_cpl_dat),
                tap_first_be.eq(0),
                tap_last_be.eq(0),
                tap_attr.eq(0),
                tap_at.eq(0),
                tap_bar_hit.eq(0),
                tap_pasid_valid.eq(0),
                tap_pasid.eq(0),
                tap_status.eq(self.tap_cpl_status),
                tap_cmp_id.eq(self.tap_cpl_cmp_id),
                tap_byte_count.eq(self.tap_cpl_byte_count),
            ),
        ]

        # Determine TLP type
        tlp_type = Signal(4)
        self.comb += [
            If(tap_is_request,
                If(tap_we,
                    tlp_type.eq(TLP_TYPE_MWR),
                ).Else(
                    tlp_type.eq(TLP_TYPE_MRD),
                ),
            ).Elif(tap_is_completion,
                If(tap_len > 0,
                    tlp_type.eq(TLP_TYPE_CPLD),
                ).Else(
                    tlp_type.eq(TLP_TYPE_CPL),
                ),
            ).Else(
                tlp_type.eq(0),
            ),
        ]

        # =====================================================================
        # Header Word Construction (combinatorial)
        # =====================================================================

        header_word0 = Signal(64)
        header_word1 = Signal(64)
        header_word2 = Signal(64)
        header_word3 = Signal(64)

        self.comb += [
            header_word0.eq(build_header_word0(
                tap_len, tlp_type, direction, self.timestamp[:32]
            )),
            header_word1.eq(build_header_word1(
                self.timestamp[32:64], tap_req_id, tap_tag, tap_first_be, tap_last_be
            )),
            header_word2.eq(build_header_word2(tap_adr)),
        ]

        if direction == DIR_RX:
            self.comb += header_word3.eq(build_header_word3_rx(
                tap_we, tap_bar_hit, tap_attr, tap_at,
                tap_status, tap_cmp_id, tap_byte_count
            ))
        else:
            self.comb += header_word3.eq(build_header_word3_tx(
                tap_we, tap_attr, tap_at, tap_pasid_valid, tap_pasid,
                tap_status, tap_cmp_id, tap_byte_count
            ))

        # Full 256-bit header
        full_header = Signal(256)
        self.comb += full_header.eq(Cat(header_word0, header_word1, header_word2, header_word3))

        # =====================================================================
        # State: just "are we dropping this packet?"
        # =====================================================================

        dropping = Signal()

        # =====================================================================
        # Header: push to FIFO on first beat (if FIFO ready)
        # =====================================================================

        # Try to write header on first beat
        first_beat = self.enable & tap_valid & tap_first

        self.comb += [
            self.header_sink.valid.eq(first_beat & ~dropping),
            self.header_sink.data.eq(full_header),
        ]

        # =====================================================================
        # Payload: push to FIFO every beat (if not dropping)
        # =====================================================================

        self.comb += [
            self.payload_sink.valid.eq(self.enable & tap_valid & ~dropping),
            self.payload_sink.data.eq(tap_dat),
        ]

        # =====================================================================
        # Drop Logic & Statistics
        # =====================================================================

        self.sync += [
            # On first beat: check if header FIFO accepted it
            If(first_beat,
                If(self.header_sink.ready,
                    # Header accepted - packet will be captured
                    self.packets_captured.eq(self.packets_captured + 1),
                    # If single-beat packet, we're done; else stay not-dropping
                    dropping.eq(0),
                ).Else(
                    # Header FIFO full - drop this packet
                    If(~tap_last,
                        # Multi-beat: need to drop remaining beats
                        dropping.eq(1),
                    ).Else(
                        # Single-beat: dropped immediately
                        self.packets_dropped.eq(self.packets_dropped + 1),
                    ),
                ),
            ),

            # On last beat while dropping: count and clear
            If(tap_valid & tap_last & dropping,
                self.packets_dropped.eq(self.packets_dropped + 1),
                dropping.eq(0),
            ),

            # Statistics clear
            If(self.clear_stats,
                self.packets_captured.eq(0),
                self.packets_dropped.eq(0),
            ),
        ]
