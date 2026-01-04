#
# USB TLP Monitor - Capture Engine
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Captures TLPs from parsed tap points and streams to header/payload FIFOs.
# Parameterized for RX (inbound) or TX (outbound) direction.
#
# Design:
# - On first beat: check header FIFO ready, latch header fields, start payload
# - On all beats: write payload to FIFO, track truncation
# - On last beat: write header with TLP length and truncated flag
# - If header FIFO not ready on first beat: drop entire packet
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

    Robust design with truncation handling:
    - Header written on LAST beat (not first) with TLP payload length
    - If payload FIFO backpressures mid-packet, packet is truncated (not dropped)
    - Truncated flag in header indicates partial capture
    - If header FIFO full on first beat, entire packet is dropped

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

        # TX only: PASID and privilege/execute bits
        self.tap_req_pasid_valid = Signal()
        self.tap_req_pasid   = Signal(20)
        self.tap_req_privileged = Signal()  # PMR (Privileged Mode Requested)
        self.tap_req_execute = Signal()     # ER (Execute Requested)

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
        self.packets_truncated = Signal(32)

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
        tap_privileged = Signal()
        tap_execute = Signal()
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
                tap_privileged.eq(self.tap_req_privileged),
                tap_execute.eq(self.tap_req_execute),
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
                tap_privileged.eq(0),
                tap_execute.eq(0),
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
        # Latched Header Fields (captured on first beat)
        # =====================================================================

        latched_tlp_type = Signal(4)
        latched_timestamp = Signal(64)
        latched_req_id = Signal(16)
        latched_tag = Signal(8)
        latched_first_be = Signal(4)
        latched_last_be = Signal(4)
        latched_adr = Signal(64)
        latched_we = Signal()
        latched_bar_hit = Signal(3)
        latched_attr = Signal(2)
        latched_at = Signal(2)
        latched_pasid_valid = Signal()
        latched_pasid = Signal(20)
        latched_privileged = Signal()
        latched_execute = Signal()
        latched_status = Signal(3)
        latched_cmp_id = Signal(16)
        latched_byte_count = Signal(12)
        latched_has_payload = Signal()

        # =====================================================================
        # Packet State
        # =====================================================================

        # Are we dropping this packet? (header FIFO was full on first beat)
        dropping = Signal()

        # Payload tracking
        payload_truncated = Signal()        # Set if any payload beat failed

        # =====================================================================
        # Beat Detection
        # =====================================================================

        first_beat = self.enable & tap_valid & tap_first
        last_beat = self.enable & tap_valid & tap_last
        any_beat = self.enable & tap_valid

        # =====================================================================
        # Payload: write to FIFO only for TLPs with payload
        # =====================================================================

        # Determine if this TLP type has payload:
        # - Requests: MWr (tap_we=1) has payload, MRd (tap_we=0) does not
        # - Completions: CPLD (tap_len>0) has payload, CPL (tap_len=0) does not
        has_payload = Signal()
        self.comb += [
            If(tap_is_request,
                has_payload.eq(tap_we),  # MWr has payload, MRd doesn't
            ).Elif(tap_is_completion,
                has_payload.eq(tap_len > 0),  # CPLD has payload, CPL doesn't
            ).Else(
                has_payload.eq(0),
            ),
        ]

        # Suppress payload when dropping OR when about to drop (header FIFO full on first beat)
        # The dropping flag is registered, so it doesn't take effect until next cycle.
        # We need start_dropping to prevent orphan payload on the first beat of a dropped packet.
        start_dropping = first_beat & ~self.header_sink.ready

        self.comb += [
            self.payload_sink.valid.eq(any_beat & has_payload & ~dropping & ~start_dropping),
            self.payload_sink.data.eq(tap_dat),
        ]

        # Track successful writes and failures
        payload_write_success = self.payload_sink.valid & self.payload_sink.ready
        payload_write_failed = self.payload_sink.valid & ~self.payload_sink.ready

        # Final truncated status includes current beat's failure (for last beat check)
        final_truncated = Signal()
        self.comb += final_truncated.eq(payload_truncated | payload_write_failed)

        # =====================================================================
        # Header Construction (uses latched values + actual payload count)
        # =====================================================================

        # Capture the TLP length field for reporting.
        # This reports the original payload length in DWORDs from the TLP header.
        latched_len = Signal(10)
        self.sync += [
            If(first_beat & self.header_sink.ready,
                latched_len.eq(tap_len),
            ),
        ]

        # For single-beat packets (first=last=1), the latched values haven't been
        # updated yet (sync takes effect next cycle). Use tap signals directly.
        # For multi-beat packets, tap signals on last beat contain payload data,
        # so we must use the latched values captured on first beat.
        single_beat = Signal()
        self.comb += single_beat.eq(first_beat & tap_last)

        use_tlp_type = Signal(4)
        use_timestamp = Signal(64)
        use_req_id = Signal(16)
        use_tag = Signal(8)
        use_first_be = Signal(4)
        use_last_be = Signal(4)
        use_adr = Signal(64)
        use_we = Signal()
        use_bar_hit = Signal(3)
        use_attr = Signal(2)
        use_at = Signal(2)
        use_pasid_valid = Signal()
        use_pasid = Signal(20)
        use_privileged = Signal()
        use_execute = Signal()
        use_status = Signal(3)
        use_cmp_id = Signal(16)
        use_byte_count = Signal(12)

        self.comb += [
            If(single_beat,
                use_tlp_type.eq(tlp_type),
                use_timestamp.eq(self.timestamp),
                use_req_id.eq(tap_req_id),
                use_tag.eq(tap_tag),
                use_first_be.eq(tap_first_be),
                use_last_be.eq(tap_last_be),
                use_adr.eq(tap_adr),
                use_we.eq(tap_we),
                use_bar_hit.eq(tap_bar_hit),
                use_attr.eq(tap_attr),
                use_at.eq(tap_at),
                use_pasid_valid.eq(tap_pasid_valid),
                use_pasid.eq(tap_pasid),
                use_privileged.eq(tap_privileged),
                use_execute.eq(tap_execute),
                use_status.eq(tap_status),
                use_cmp_id.eq(tap_cmp_id),
                use_byte_count.eq(tap_byte_count),
            ).Else(
                use_tlp_type.eq(latched_tlp_type),
                use_timestamp.eq(latched_timestamp),
                use_req_id.eq(latched_req_id),
                use_tag.eq(latched_tag),
                use_first_be.eq(latched_first_be),
                use_last_be.eq(latched_last_be),
                use_adr.eq(latched_adr),
                use_we.eq(latched_we),
                use_bar_hit.eq(latched_bar_hit),
                use_attr.eq(latched_attr),
                use_at.eq(latched_at),
                use_pasid_valid.eq(latched_pasid_valid),
                use_pasid.eq(latched_pasid),
                use_privileged.eq(latched_privileged),
                use_execute.eq(latched_execute),
                use_status.eq(latched_status),
                use_cmp_id.eq(latched_cmp_id),
                use_byte_count.eq(latched_byte_count),
            ),
        ]

        # Build header words
        header_word0 = Signal(64)
        header_word1 = Signal(64)
        header_word2 = Signal(64)
        header_word3 = Signal(64)

        self.comb += [
            header_word0.eq(build_header_word0(
                Mux(single_beat,
                    Mux(has_payload, tap_len, 0),
                    Mux(latched_has_payload, latched_len, 0),
                ),
                use_tlp_type, direction,
                use_timestamp[:32], final_truncated
            )),
            header_word1.eq(build_header_word1(
                use_timestamp[32:64], use_req_id, use_tag,
                use_first_be, use_last_be
            )),
            header_word2.eq(build_header_word2(use_adr)),
        ]

        if direction == DIR_RX:
            self.comb += header_word3.eq(build_header_word3_rx(
                use_we, use_bar_hit, use_attr, use_at,
                use_status, use_cmp_id, use_byte_count
            ))
        else:
            self.comb += header_word3.eq(build_header_word3_tx(
                use_we, use_attr, use_at, use_pasid_valid, use_pasid,
                use_privileged, use_execute, use_status, use_cmp_id, use_byte_count
            ))

        # Full 256-bit header
        full_header = Signal(256)
        self.comb += full_header.eq(Cat(header_word0, header_word1, header_word2, header_word3))

        # =====================================================================
        # Header: write to FIFO on LAST beat (if not dropping)
        # =====================================================================

        self.comb += [
            self.header_sink.valid.eq(last_beat & ~dropping),
            self.header_sink.data.eq(full_header),
        ]

        # =====================================================================
        # State Machine & Statistics
        # =====================================================================

        # Detect single-beat drops (first=last=1 when header FIFO full)
        # Need combinatorial signal since dropping flag doesn't take effect until next cycle
        single_beat_drop = Signal()
        self.comb += single_beat_drop.eq(first_beat & tap_last & ~self.header_sink.ready)

        self.sync += [
            # On first beat: decide whether to capture or drop, initialize counters
            If(first_beat,
                If(self.header_sink.ready,
                    # Header FIFO has room - proceed with capture
                    dropping.eq(0),

                    payload_truncated.eq(payload_write_failed),

                    # Latch header fields
                    latched_tlp_type.eq(tlp_type),
                    latched_timestamp.eq(self.timestamp),
                    latched_req_id.eq(tap_req_id),
                    latched_tag.eq(tap_tag),
                    latched_first_be.eq(tap_first_be),
                    latched_last_be.eq(tap_last_be),
                    latched_adr.eq(tap_adr),
                    latched_we.eq(tap_we),
                    latched_bar_hit.eq(tap_bar_hit),
                    latched_attr.eq(tap_attr),
                    latched_at.eq(tap_at),
                    latched_pasid_valid.eq(tap_pasid_valid),
                    latched_pasid.eq(tap_pasid),
                    latched_privileged.eq(tap_privileged),
                    latched_execute.eq(tap_execute),
                    latched_status.eq(tap_status),
                    latched_cmp_id.eq(tap_cmp_id),
                    latched_byte_count.eq(tap_byte_count),
                    latched_has_payload.eq(has_payload),
                ).Else(
                    # Header FIFO full - drop entire packet
                    # For multi-beat: set dropping flag (count on last beat)
                    # For single-beat: count handled by last_beat block via single_beat_drop
                    If(~tap_last,
                        dropping.eq(1),
                    ),
                ),
            ),

            # Track payload write failures on non-first beats (when not dropping)
            If(any_beat & ~first_beat & ~dropping,
                If(payload_write_failed,
                    payload_truncated.eq(1),
                ),
            ),

            # On last beat: finalize statistics
            If(last_beat,
                If(dropping | single_beat_drop,
                    # Was dropping or single-beat drop - count as dropped
                    self.packets_dropped.eq(self.packets_dropped + 1),
                    dropping.eq(0),
                ).Else(
                    # Captured (possibly truncated)
                    self.packets_captured.eq(self.packets_captured + 1),
                    If(final_truncated,
                        self.packets_truncated.eq(self.packets_truncated + 1),
                    ),
                ),
            ),

            # Statistics clear
            If(self.clear_stats,
                self.packets_captured.eq(0),
                self.packets_dropped.eq(0),
                self.packets_truncated.eq(0),
            ),
        ]
