#
# BSA PCIe Exerciser - ATS Invalidation Handler
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Handles ATS Invalidation Requests from the host IOMMU.
# Coordinates with ATC and ATS engine for proper invalidation.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import phy_layout
from litepcie.tlp.common import tlp_ats_inv_layout


class ATSInvalidationHandler(LiteXModule):
    """
    ATS Invalidation Request Handler for BSA Exerciser.

    Receives ATS Invalidation Request Messages from the host IOMMU and:
    1. Checks if invalidation range overlaps with ATC entry
    2. Coordinates with ATS engine if translation is in flight
    3. Coordinates with DMA engine if translated DMA is in progress
    4. Invalidates ATC when safe
    5. Sends Invalidation Completion response

    Per ARM BSA Exerciser spec, invalidation scenarios:
    - If ATC empty: Return immediately
    - If invalidation range contains ATC input address AND substreamID matches:
      - If ATS request in flight: Discard result, invalidate ATC, retry
      - If DMA in progress using address: Wait for completion, then invalidate
      - Otherwise: Invalidate immediately

    Attributes
    ----------
    inv_sink : stream.Endpoint
        Invalidation Request input (from depacketizer message path).

    cpl_source : stream.Endpoint
        Invalidation Completion output (to packetizer).

    atc_valid : Signal, in
        ATC entry is valid.

    atc_input_addr : Signal(64), in
        ATC entry input (untranslated) address.

    atc_range_size : Signal(32), in
        ATC entry range size.

    atc_pasid_valid : Signal, in
        ATC entry has valid PASID.

    atc_pasid_val : Signal(20), in
        ATC entry PASID value.

    atc_invalidate : Signal, out
        Pulse to invalidate ATC.

    ats_in_flight : Signal, in
        ATS translation request is in progress.

    ats_retry : Signal, out
        Signal ATS engine to retry current translation.

    dma_busy : Signal, in
        DMA engine is busy.

    dma_using_atc : Signal, in
        DMA is using ATC-translated address.

    invalidated : Signal, out
        Pulse when invalidation occurred.
    """

    def __init__(self, phy, data_width=64):
        self.phy = phy
        self.data_width = data_width

        # =====================================================================
        # Stream Interfaces
        # =====================================================================

        # Invalidation Request input (ATS Invalidation Message TLP)
        self.inv_sink = stream.Endpoint(tlp_ats_inv_layout(data_width))

        # Invalidation Completion output (raw Message TLP via phy_layout)
        # ATS Invalidation Completion is a Message TLP, NOT a Completion TLP:
        #   - Fmt/Type = 001 10010 (4DW, Message routed by ID)
        #   - Message Code = 0x02 (Invalidation Completion)
        self.msg_source = stream.Endpoint(phy_layout(data_width))

        # =====================================================================
        # ATC Interface
        # =====================================================================

        self.atc_valid          = Signal()
        self.atc_input_addr     = Signal(64)
        self.atc_input_addr_end = Signal(64)  # Precomputed from ATC
        self.atc_range_size     = Signal(32)
        self.atc_pasid_valid    = Signal()
        self.atc_pasid_val      = Signal(20)
        self.atc_invalidate     = Signal()

        # =====================================================================
        # ATS Engine Interface
        # =====================================================================

        self.ats_in_flight = Signal()
        self.ats_retry     = Signal()

        # =====================================================================
        # DMA Engine Interface
        # =====================================================================

        self.dma_busy      = Signal()
        self.dma_using_atc = Signal()

        # =====================================================================
        # Status
        # =====================================================================

        self.invalidated = Signal()

        # # #

        # =====================================================================
        # Internal Signals
        # =====================================================================

        # Latched invalidation request parameters
        inv_addr      = Signal(64)
        inv_size      = Signal(32)
        inv_pasid_valid = Signal()
        inv_pasid_val = Signal(20)
        inv_global    = Signal()  # Global PASID invalidation (G-bit)
        inv_req_id    = Signal(16)
        inv_tag       = Signal(8)
        inv_itag      = Signal(5)  # Invalidation tag for completion

        # Range overlap check
        # Uses precomputed atc_input_addr_end from ATC (computed at store time)
        inv_end_addr = Signal(64)
        ranges_overlap = Signal()

        self.comb += [
            inv_end_addr.eq(inv_addr + inv_size - 1),
            # Ranges overlap if: start1 <= end2 AND start2 <= end1
            ranges_overlap.eq(
                (self.atc_input_addr <= inv_end_addr) &
                (inv_addr <= self.atc_input_addr_end)
            ),
        ]

        # PASID match check
        pasid_match = Signal()
        self.comb += pasid_match.eq(
            inv_global |  # Global invalidation affects all PASIDs
            (~self.atc_pasid_valid & ~inv_pasid_valid) |  # Both have no PASID
            (self.atc_pasid_valid & inv_pasid_valid &
             (self.atc_pasid_val == inv_pasid_val))  # PASID values match
        )

        # Should invalidate check
        should_invalidate = Signal()
        self.comb += should_invalidate.eq(
            self.atc_valid & ranges_overlap & pasid_match
        )

        # =====================================================================
        # ATS Invalidation Completion Message TLP Header Construction
        # =====================================================================
        # Format: 4DW header, no data (Message routed by ID)
        #   DW0: [Fmt=001][Type=10010][TC=0][TD=0][EP=0][Attr=0][AT=0][Length=0]
        #   DW1: [Requester ID (ours)][Tag][Message Code = 0x02]
        #   DW2: [Target Device ID][Reserved:8][ITag:5|CC:3]
        #   DW3: [Reserved = 0]

        msg_dw0 = Signal(32)
        msg_dw1 = Signal(32)
        msg_dw2 = Signal(32)
        msg_dw3 = Signal(32)

        self.comb += [
            # DW0: Fmt=001 (4DW no data), Type=10010 (Message routed by ID)
            msg_dw0.eq(
                (0b001 << 29) |      # Fmt = 001 (4DW, no data)
                (0b10010 << 24)      # Type = 10010 (Message routed by ID)
                # TC, TD, EP, Attr, AT, Length all 0
            ),
            # DW1: [Requester ID:16][Tag:8][Message Code:8]
            msg_dw1.eq(
                (phy.id << 16) |     # Our Requester ID
                (inv_tag << 8) |     # Tag from original request
                0x02                 # Message Code = Invalidation Completion
            ),
            # DW2: [Target Device ID:16][Reserved:8][ITag:5|CC:3]
            msg_dw2.eq(
                (inv_req_id << 16) |         # Target = who sent the invalidation request
                ((inv_itag & 0x1F) << 3)     # ITag in bits [7:3], CC=000 (success)
            ),
            # DW3: Reserved
            msg_dw3.eq(0),
        ]

        # FSM state for 2-beat Message TLP output
        msg_beat = Signal()  # 0 = first beat (DW0/DW1), 1 = second beat (DW2/DW3)

        # =====================================================================
        # FSM
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # ---------------------------------------------------------------------
        # IDLE: Wait for invalidation request
        # ---------------------------------------------------------------------

        fsm.act("IDLE",
            self.inv_sink.ready.eq(1),

            If(self.inv_sink.valid & self.inv_sink.first,
                # Latch request parameters from ATS Invalidation Message
                # For 32-bit addresses, DW3 contains Address[31:0] directly
                # For 64-bit addresses, DW3 contains Address[63:32] (upper bits)
                # Since BSA exerciser typically uses 32-bit addresses, use address as-is
                # and page-align by masking lower 12 bits
                NextValue(inv_addr, self.inv_sink.address & 0xFFFFF000),
                # S-bit: 0=4KB page, 1=size comes from data payload
                NextValue(inv_size, Mux(self.inv_sink.s_bit, 0, 4096)),
                NextValue(inv_req_id, self.inv_sink.requester_id),
                NextValue(inv_tag, self.inv_sink.tag),
                NextValue(inv_itag, self.inv_sink.itag),
                # G-bit: Global invalidation (ignore PASID matching)
                NextValue(inv_global, self.inv_sink.g_bit),
                # PASID not in basic ATS Invalidation; would require TLP prefix
                NextValue(inv_pasid_valid, 0),
                NextValue(inv_pasid_val, 0),

                If(self.inv_sink.last,
                    NextState("CHECK"),
                ).Else(
                    NextState("RECEIVE"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # RECEIVE: Receive remaining beats of invalidation request
        # When S-bit=1, the data payload contains the invalidation range size
        # ---------------------------------------------------------------------

        fsm.act("RECEIVE",
            self.inv_sink.ready.eq(1),

            If(self.inv_sink.valid,
                # If S-bit was set, extract range size from first data DWORD
                # Format: Address[63:32] in header, Address[31:12] + Size in data
                If(inv_size == 0,
                    # Size encoding: 2^(N+12) where N is from data payload
                    # For simplicity, extract lower address bits and assume 4KB for now
                    # TODO: Parse full ATS Invalidation data format
                    NextValue(inv_size, 4096),
                ),

                If(self.inv_sink.last,
                    NextState("CHECK"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # CHECK: Check if invalidation affects ATC
        # ---------------------------------------------------------------------

        fsm.act("CHECK",
            If(~self.atc_valid,
                # ATC empty, nothing to invalidate
                NextState("SEND_CPL"),
            ).Elif(~should_invalidate,
                # No overlap, nothing to invalidate
                NextState("SEND_CPL"),
            ).Elif(self.ats_in_flight,
                # ATS translation in progress for overlapping range
                # Signal retry and wait
                self.ats_retry.eq(1),
                NextState("WAIT_ATS"),
            ).Elif(self.dma_busy & self.dma_using_atc,
                # DMA using translated address from ATC
                # Wait for DMA to complete
                NextState("WAIT_DMA"),
            ).Else(
                # Safe to invalidate immediately
                NextState("INVALIDATE"),
            ),
        )

        # ---------------------------------------------------------------------
        # WAIT_ATS: Wait for ATS translation to complete/abort
        # ---------------------------------------------------------------------

        fsm.act("WAIT_ATS",
            If(~self.ats_in_flight,
                NextState("INVALIDATE"),
            ),
        )

        # ---------------------------------------------------------------------
        # WAIT_DMA: Wait for DMA to complete
        # ---------------------------------------------------------------------

        fsm.act("WAIT_DMA",
            If(~self.dma_busy,
                NextState("INVALIDATE"),
            ),
        )

        # ---------------------------------------------------------------------
        # INVALIDATE: Perform ATC invalidation
        # ---------------------------------------------------------------------

        fsm.act("INVALIDATE",
            self.atc_invalidate.eq(1),
            self.invalidated.eq(1),
            NextState("SEND_CPL"),
        )

        # ---------------------------------------------------------------------
        # SEND_CPL: Send ATS Invalidation Completion as Message TLP
        # 4DW header requires 2 beats on 64-bit bus:
        #   Beat 0: DW0 (lower 32), DW1 (upper 32) with first=1, last=0
        #   Beat 1: DW2 (lower 32), DW3 (upper 32) with first=0, last=1
        # ---------------------------------------------------------------------

        fsm.act("SEND_CPL",
            self.msg_source.valid.eq(1),
            self.msg_source.first.eq(~msg_beat),  # first=1 on beat 0
            self.msg_source.last.eq(msg_beat),    # last=1 on beat 1

            # Output DWORDs based on current beat
            If(~msg_beat,
                # First beat: DW0 (Fmt/Type) and DW1 (Req ID, Tag, Msg Code)
                self.msg_source.dat[0:32].eq(msg_dw0),
                self.msg_source.dat[32:64].eq(msg_dw1),
                self.msg_source.be.eq(0xFF),  # All bytes valid
            ).Else(
                # Second beat: DW2 (Target ID, ITag, CC) and DW3 (Reserved)
                self.msg_source.dat[0:32].eq(msg_dw2),
                self.msg_source.dat[32:64].eq(msg_dw3),
                self.msg_source.be.eq(0xFF),  # All bytes valid
            ),

            If(self.msg_source.ready,
                If(~msg_beat,
                    # First beat accepted, move to second
                    NextValue(msg_beat, 1),
                ).Else(
                    # Second beat accepted, done
                    NextValue(msg_beat, 0),
                    NextState("IDLE"),
                ),
            ),
        )
