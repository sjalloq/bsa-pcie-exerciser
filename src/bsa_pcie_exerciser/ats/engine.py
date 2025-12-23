#
# BSA PCIe Exerciser - ATS Engine
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Generates ATS Translation Request TLPs and handles Translation Completions.
# Stores results in ATC and exposes to BSARegisters.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import request_layout, completion_layout


# Reserved tag range for ATS requests (0xF0-0xFF)
ATS_TAG_BASE = 0xF0


class ATSEngine(LiteXModule):
    """
    ATS (Address Translation Services) Engine for BSA Exerciser.

    Generates ATS Translation Request TLPs and parses Translation Completions.
    Stores translation results for use by DMA engine via ATC.

    ATS Translation Request format:
        - Memory Read with AT=01 (Translation Request)
        - Length: 1 DWORD (minimum, for STU encoding)
        - Address: Untranslated virtual address
        - first_be[3]: No-Write hint (1 = read-only permission requested)
        - Uses reserved tag range 0xF0-0xFF

    Translation Completion format (in completion data):
        - Translated address (page-aligned)
        - S field: Size encoding (2^(S+12) bytes, minimum 4KB)
        - Permission bits: N (no-snoop), U (untranslated), W (write), R (read)

    Attributes
    ----------
    trigger : Signal, in
        Pulse to start ATS translation request.

    address : Signal(64), in
        Untranslated (virtual) address to translate.

    pasid_en : Signal, in
        Include PASID in ATS request.

    pasid_val : Signal(20), in
        PASID value.

    privileged : Signal, in
        Privileged mode request.

    no_write : Signal, in
        Request read-only permission (no-write hint).

    exec_req : Signal, in
        Request execute permission.

    clear_atc : Signal, in
        Clear ATC and reset results.

    in_flight : Signal, out
        ATS request is in progress.

    success : Signal, out
        Translation completed successfully.

    cacheable : Signal, out
        Translation result is cacheable (R/W != 0).

    invalidated : Signal, out
        ATC was invalidated.

    translated_addr : Signal(64), out
        Translated physical address.

    range_size : Signal(32), out
        Size of translated region in bytes.

    permissions : Signal(8), out
        Permission bits [6:0] = {read_priv, 0, write_priv, exec_priv, read, write, exec}
    """

    def __init__(self, phy, data_width=64):
        assert data_width >= 64, "Minimum 64-bit data width"

        self.phy = phy
        self.data_width = data_width

        # =====================================================================
        # Control Interface (from BSARegisters)
        # =====================================================================

        self.trigger    = Signal()           # Pulse to start ATS request
        self.address    = Signal(64)         # Untranslated address
        self.pasid_en   = Signal()           # Include PASID
        self.pasid_val  = Signal(20)         # PASID value
        self.privileged = Signal()           # Privileged mode
        self.no_write   = Signal()           # Read-only request
        self.exec_req   = Signal()           # Execute permission request
        self.clear_atc  = Signal()           # Clear ATC
        self.retry      = Signal()           # Retry current translation (from invalidation)

        # =====================================================================
        # Status Interface (to BSARegisters)
        # =====================================================================

        self.in_flight   = Signal()          # Request in progress
        self.success     = Signal()          # Translation successful
        self.cacheable   = Signal()          # Result cacheable (RW != 0)
        self.invalidated = Signal()          # ATC was invalidated

        # =====================================================================
        # Result Interface (to BSARegisters and ATC)
        # =====================================================================

        self.translated_addr = Signal(64)    # Translated address
        self.range_size      = Signal(32)    # Range size in bytes
        self.permissions     = Signal(8)     # Permission bits

        # Result write enable (pulse when results are valid)
        self.result_we = Signal()

        # =====================================================================
        # Master Port Interface (TLP generation)
        # =====================================================================

        self.source = source = stream.Endpoint(request_layout(data_width))
        self.sink   = sink   = stream.Endpoint(completion_layout(data_width))

        # =====================================================================
        # PASID Output Interface (to Prefix Injector)
        # =====================================================================

        self.pasid_out_en   = Signal()
        self.pasid_out_val  = Signal(20)
        self.pasid_out_priv = Signal()
        self.pasid_out_exec = Signal()

        # =====================================================================
        # Internal Signals
        # =====================================================================

        # Latched control parameters
        current_addr      = Signal(64)
        current_pasid_en  = Signal()
        current_pasid_val = Signal(20)
        current_priv      = Signal()
        current_no_write  = Signal()
        current_exec_req  = Signal()

        # Transaction tag (within reserved range)
        current_tag = Signal(8, reset=ATS_TAG_BASE)

        # Timeout counter (~134ms at 125MHz)
        timeout_counter = Signal(24)
        timeout_expired = Signal()
        self.comb += timeout_expired.eq(timeout_counter == 0xFFFFFF)

        # Completion data parsing
        cpl_data = Signal(64)
        cpl_valid = Signal()

        # Connect PASID output signals
        self.comb += [
            self.pasid_out_en.eq(current_pasid_en),
            self.pasid_out_val.eq(current_pasid_val),
            self.pasid_out_priv.eq(current_priv),
            self.pasid_out_exec.eq(current_exec_req),
        ]

        # =====================================================================
        # FSM
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # ---------------------------------------------------------------------
        # IDLE: Wait for trigger
        # ---------------------------------------------------------------------

        fsm.act("IDLE",
            self.in_flight.eq(0),

            If(self.clear_atc,
                # Clear ATC and results
                NextValue(self.success, 0),
                NextValue(self.cacheable, 0),
                NextValue(self.translated_addr, 0),
                NextValue(self.range_size, 0),
                NextValue(self.permissions, 0),
            ),

            If(self.trigger,
                # Latch parameters
                NextValue(current_addr, self.address),
                NextValue(current_pasid_en, self.pasid_en),
                NextValue(current_pasid_val, self.pasid_val),
                NextValue(current_priv, self.privileged),
                NextValue(current_no_write, self.no_write),
                NextValue(current_exec_req, self.exec_req),
                # Clear previous status
                NextValue(self.success, 0),
                NextValue(self.invalidated, 0),
                NextState("SEND_REQ"),
            ),
        )

        # ---------------------------------------------------------------------
        # SEND_REQ: Issue ATS Translation Request TLP
        # ---------------------------------------------------------------------

        # Common request fields
        self.comb += [
            source.channel.eq(0),
            source.req_id.eq(phy.id),
            source.tag.eq(current_tag),
            source.attr.eq(0),  # No special attributes for ATS
            source.at.eq(0b01),  # AT=01: Translation Request
            # No-write hint in first_be[3]
            source.first_be.eq(Cat(0b111, current_no_write)),
            source.last_be.eq(0xF),
            # PASID fields
            source.pasid_en.eq(current_pasid_en),
            source.pasid_val.eq(current_pasid_val),
            source.privileged.eq(current_priv),
            source.execute.eq(current_exec_req),
        ]

        fsm.act("SEND_REQ",
            self.in_flight.eq(1),

            source.valid.eq(1),
            source.first.eq(1),
            source.last.eq(1),
            source.we.eq(0),  # Read request (Translation Request)
            source.adr.eq(current_addr),
            source.len.eq(1),  # 1 DWORD (minimum for ATS)

            If(source.ready,
                NextValue(current_tag, current_tag + 1),
                # Wrap tag within reserved range
                If(current_tag == 0xFF,
                    NextValue(current_tag, ATS_TAG_BASE),
                ),
                NextValue(timeout_counter, 0),
                NextState("WAIT_CPL"),
            ),
        )

        # ---------------------------------------------------------------------
        # WAIT_CPL: Wait for Translation Completion
        # ---------------------------------------------------------------------

        fsm.act("WAIT_CPL",
            self.in_flight.eq(1),
            sink.ready.eq(1),

            # Timeout counter
            NextValue(timeout_counter, timeout_counter + 1),

            # Handle retry request from invalidation handler
            If(self.retry,
                # Discard current completion and restart
                NextValue(timeout_counter, 0),
                NextState("SEND_REQ"),
            ).Elif(timeout_expired,
                # Timeout error
                NextValue(self.success, 0),
                NextState("IDLE"),
            ).Elif(sink.valid,
                # Reset timeout on completion data
                NextValue(timeout_counter, 0),

                If(sink.err,
                    # Completion error (UR, CA)
                    NextValue(self.success, 0),
                    NextState("IDLE"),
                ).Else(
                    # Store completion data for parsing
                    NextValue(cpl_data, sink.dat),
                    NextValue(cpl_valid, 1),

                    If(sink.last & sink.end,
                        NextState("PARSE_CPL"),
                    ),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # PARSE_CPL: Parse Translation Completion data
        # ---------------------------------------------------------------------

        # ATS Translation Completion payload format (64-bit):
        # Bits [63:12]: Translated address (page-aligned, lower 12 bits are 0)
        # Bits [11:6]:  S field (size = 2^(S+12) bytes)
        # Bit  [5]:     N (No-snoop)
        # Bit  [4]:     U (Untranslated - error)
        # Bit  [3]:     Global PASID
        # Bit  [2]:     Privileged mode result
        # Bit  [1]:     W (Write permission)
        # Bit  [0]:     R (Read permission)

        trans_addr = Signal(64)
        s_field    = Signal(5)  # S-field is 5 bits per PCIe ATS spec (bits [10:6])
        n_bit      = Signal()
        u_bit      = Signal()
        w_bit      = Signal()
        r_bit      = Signal()

        self.comb += [
            trans_addr.eq(cpl_data & 0xFFFFFFFFFFFFF000),  # Page-aligned
            s_field.eq((cpl_data >> 6) & 0x1F),  # 5 bits per spec
            n_bit.eq((cpl_data >> 5) & 1),
            u_bit.eq((cpl_data >> 4) & 1),
            w_bit.eq((cpl_data >> 1) & 1),
            r_bit.eq(cpl_data & 1),
        ]

        fsm.act("PARSE_CPL",
            self.in_flight.eq(1),
            self.result_we.eq(1),

            If(u_bit,
                # U=1 means translation failed
                NextValue(self.success, 0),
                NextValue(self.cacheable, 0),
            ).Else(
                # Translation successful
                NextValue(self.success, 1),
                NextValue(self.translated_addr, trans_addr),
                # Range size = 2^(S+12) bytes
                NextValue(self.range_size, 1 << (s_field + 12)),
                # Cacheable if R or W permission granted
                NextValue(self.cacheable, r_bit | w_bit),
                # Store permissions: [6]=read_priv, [4]=write_priv, [3]=exec_priv,
                #                    [2]=read, [1]=write, [0]=exec
                # Note: Execute permission requires separate handling
                NextValue(self.permissions, Cat(
                    0,      # [0] exec (not in basic ATS)
                    w_bit,  # [1] write
                    r_bit,  # [2] read
                    0,      # [3] exec_priv
                    0,      # [4] write_priv (would need priv response)
                    0,      # [5] reserved
                    0,      # [6] read_priv
                    0,      # [7] reserved
                )),
            ),

            NextState("IDLE"),
        )
