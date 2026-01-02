#
# BSA PCIe Exerciser - DMA Engine
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# DMA engine for BSA exerciser. Performs controlled DMA transfers between
# host memory and the internal buffer with configurable TLP attributes.
#
# Supports:
#   - DMA reads (host → exerciser): Memory Read TLP, store completion data
#   - DMA writes (exerciser → host): Memory Write TLP, no completion
#   - TLP attributes: No-Snoop, Address Type
#   - Multi-TLP transfers (split at MAX_REQUEST_SIZE)
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import *


# Maximum request/payload size in bytes (configurable, must match PCIe negotiated value)
MAX_REQUEST_SIZE = 128


class BSADMAEngine(LiteXModule):
    """
    DMA Engine for BSA PCIe Exerciser.

    Performs DMA transfers between host memory and the internal buffer.
    Controlled via BSARegisters interface signals.

    DMA Read (host → exerciser, direction=0):
        1. Issue Memory Read TLP to host address
        2. Receive completion(s) with data
        3. Store data in internal buffer at configured offset

    DMA Write (exerciser → host, direction=1):
        1. Read data from internal buffer
        2. Issue Memory Write TLP to host address
        3. No completion expected (posted write)

    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance (for requester ID).

    buffer : BSADMABuffer
        The dual-port RAM buffer instance.

    data_width : int
        PCIe data width (default 64).

    max_request_size : int
        Maximum request size in bytes (default 128).
    """

    def __init__(self, phy, buffer, data_width=64, max_request_size=MAX_REQUEST_SIZE):
        assert data_width >= 64, "Minimum 64-bit data width"

        self.phy = phy
        self.data_width = data_width
        self.max_request_size = max_request_size

        # =====================================================================
        # Control Interface (from BSARegisters)
        # =====================================================================

        self.trigger     = Signal()          # Pulse to start DMA
        self.direction   = Signal()          # 0=read from host, 1=write to host
        self.no_snoop    = Signal()          # TLP No-Snoop attribute
        self.addr_type   = Signal(2)         # TLP Address Type (AT field)
        self.bus_addr    = Signal(64)        # Host memory address
        self.length      = Signal(32)        # Transfer length in bytes
        self.offset      = Signal(32)        # Offset within internal buffer

        # PASID TLP Prefix control
        self.pasid_en    = Signal()          # Enable PASID TLP prefix
        self.pasid_val   = Signal(20)        # 20-bit PASID value
        self.privileged  = Signal()          # Privileged Mode Requested (PMR)
        self.instruction = Signal()          # Instruction access (maps to Execute)

        # =====================================================================
        # Status Interface (to BSARegisters)
        # =====================================================================

        self.busy        = Signal()          # DMA in progress
        self.status      = Signal(2)         # Status: 0=ok, 1=error, 2=timeout
        self.status_we   = Signal()          # Pulse when status is valid

        # =====================================================================
        # PASID Output Interface (to Prefix Injector)
        # =====================================================================
        # These are latched values, stable during DMA operation
        self.pasid_out_en   = Signal()       # Latched PASID enable
        self.pasid_out_val  = Signal(20)     # Latched PASID value
        self.pasid_out_priv = Signal()       # Latched privileged mode
        self.pasid_out_exec = Signal()       # Latched execute

        # =====================================================================
        # ATC Lookup Interface (from ATC)
        # =====================================================================
        # Used when use_atc=1 to substitute translated address for bus_addr
        # The top-level drives ATC lookup inputs from lookup_addr and PASID signals,
        # then connects the ATC lookup results here.
        self.lookup_addr     = Signal(64)    # Address for ATC lookup (output to ATC)
        self.atc_hit         = Signal()      # ATC lookup hit (includes PASID match)
        self.atc_output_addr = Signal(64)    # ATC translated (physical) address
        self.use_atc         = Signal()      # Enable ATC lookup for this transfer

        # =====================================================================
        # Master Port Interface (TLP generation)
        # =====================================================================

        self.source = source = stream.Endpoint(request_layout(data_width))
        self.sink   = sink   = stream.Endpoint(completion_layout(data_width))

        # =====================================================================
        # Internal Signals
        # =====================================================================

        # Latched control parameters
        current_addr   = Signal(64)          # Current host address
        current_offset = Signal(14)          # Current buffer offset (word address)
        remaining_len  = Signal(32)          # Remaining bytes to transfer
        current_dir    = Signal()            # Latched direction
        current_ns     = Signal()            # Latched no-snoop
        current_at     = Signal(2)           # Latched address type

        # Latched PASID parameters
        current_pasid_en  = Signal()         # Latched PASID enable
        current_pasid_val = Signal(20)       # Latched PASID value
        current_priv      = Signal()         # Latched privileged mode
        current_exec      = Signal()         # Latched execute (instruction)

        # Per-TLP tracking
        tlp_len_bytes  = Signal(10)          # Bytes in current TLP (max 512)
        tlp_len_dwords = Signal(8)           # DWORDs in current TLP
        beat_count     = Signal(8)           # Current beat within TLP
        current_tag    = Signal(8)           # Transaction tag

        # Completion tracking
        cpl_bytes_expected = Signal(12)      # Bytes expected for current request
        cpl_bytes_received = Signal(12)      # Bytes received so far

        # Timeout counter (for read completions)
        timeout_counter = Signal(24)         # ~134ms at 125MHz
        timeout_expired = Signal()
        self.comb += timeout_expired.eq(timeout_counter == 0xFFFFFF)

        # Calculate bytes per beat
        bytes_per_beat = data_width // 8

        # Connect PASID output signals to latched values
        self.comb += [
            self.pasid_out_en.eq(current_pasid_en),
            self.pasid_out_val.eq(current_pasid_val),
            self.pasid_out_priv.eq(current_priv),
            self.pasid_out_exec.eq(current_exec),
        ]

        # =====================================================================
        # ATC Lookup Logic
        # =====================================================================
        # Drive lookup_addr for top-level to use with ATC lookup interface.
        # The top-level connects this to atc.lookup_addr along with PASID signals,
        # and returns the hit result and translated address.
        #
        # TIMING: The ATC has a 1-cycle pipeline - lookup_addr on cycle N yields
        # lookup_hit/lookup_output on cycle N+1. The SETUP state provides this
        # latency: current_addr is stable during SETUP, ATC computes and registers
        # the result, which is then valid when we enter ISSUE_RD or ISSUE_WR.
        #
        # Note: AT field stays under software control for testing SMMU error paths

        addr_in_atc = Signal()

        self.comb += [
            # Drive lookup address for ATC (top-level connects to atc.lookup_addr)
            self.lookup_addr.eq(current_addr),

            # ATC hit comes from the ATC's PASID-aware lookup (via top-level wiring)
            # Only consider hit when use_atc is enabled
            addr_in_atc.eq(self.use_atc & self.atc_hit),
        ]

        # Effective address for TLP generation (uses translation when ATC hit)
        # The translated address comes from ATC's lookup_output (via atc_output_addr)
        effective_addr = Signal(64)
        self.comb += effective_addr.eq(
            Mux(addr_in_atc, self.atc_output_addr, current_addr)
        )

        # =====================================================================
        # FSM
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # ---------------------------------------------------------------------
        # IDLE State: Wait for trigger
        # ---------------------------------------------------------------------

        fsm.act("IDLE",
            self.busy.eq(0),

            If(self.trigger,
                # Latch parameters
                NextValue(current_addr, self.bus_addr),
                NextValue(current_offset, self.offset[3:17]),  # Word address (64-bit)
                NextValue(remaining_len, self.length),
                NextValue(current_dir, self.direction),
                NextValue(current_ns, self.no_snoop),
                NextValue(current_at, self.addr_type),
                NextValue(current_tag, 0),
                # Latch PASID parameters
                NextValue(current_pasid_en, self.pasid_en),
                NextValue(current_pasid_val, self.pasid_val),
                NextValue(current_priv, self.privileged),
                NextValue(current_exec, self.instruction),
                NextState("SETUP"),
            ),
        )

        # ---------------------------------------------------------------------
        # SETUP State: Calculate TLP size for this iteration
        # ---------------------------------------------------------------------

        fsm.act("SETUP",
            self.busy.eq(1),

            # Calculate bytes for this TLP (min of remaining and max_request_size)
            If(remaining_len > max_request_size,
                NextValue(tlp_len_bytes, max_request_size),
                NextValue(tlp_len_dwords, max_request_size >> 2),
            ).Else(
                NextValue(tlp_len_bytes, remaining_len[:10]),
                NextValue(tlp_len_dwords, remaining_len[2:10]),
            ),
            NextValue(beat_count, 0),
            NextValue(cpl_bytes_received, 0),

            If(current_dir,
                # Write to host - load data first
                NextState("LOAD_DATA"),
            ).Else(
                # Read from host - issue request
                NextState("ISSUE_RD"),
            ),
        )

        # ---------------------------------------------------------------------
        # READ PATH: Issue Memory Read, wait for completions
        # ---------------------------------------------------------------------

        # Common TLP header fields
        self.comb += [
            source.channel.eq(0),
            source.req_id.eq(phy.id),
            source.tag.eq(current_tag),
            source.attr.eq(Cat(current_ns, 0)),  # [0]=No-Snoop, [1]=RO (unused)
            source.at.eq(current_at),
            source.first_be.eq(0xF),
            source.last_be.eq(0xF),
            # PASID TLP Prefix fields
            source.pasid_en.eq(current_pasid_en),
            source.pasid_val.eq(current_pasid_val),
            source.privileged.eq(current_priv),
            source.execute.eq(current_exec),
        ]

        fsm.act("ISSUE_RD",
            self.busy.eq(1),

            source.valid.eq(1),
            source.first.eq(1),
            source.last.eq(1),
            source.we.eq(0),  # Read request
            source.adr.eq(effective_addr),  # Use ATC translation if available
            source.len.eq(tlp_len_dwords),

            If(source.ready,
                NextValue(current_tag, current_tag + 1),
                NextValue(cpl_bytes_expected, tlp_len_bytes),
                NextValue(timeout_counter, 0),
                NextState("WAIT_CPL"),
            ),
        )

        fsm.act("WAIT_CPL",
            self.busy.eq(1),
            sink.ready.eq(1),

            # Timeout counter
            NextValue(timeout_counter, timeout_counter + 1),

            If(timeout_expired,
                # Timeout error
                NextValue(self.status, 0b10),
                NextState("COMPLETE"),
            ).Elif(sink.valid,
                # Reset timeout on any completion data
                NextValue(timeout_counter, 0),

                If(sink.err,
                    # Completion error
                    NextValue(self.status, 0b01),
                    NextState("COMPLETE"),
                ).Else(
                    # Store data to buffer
                    buffer.a_adr.eq(current_offset),
                    buffer.a_dat_w.eq(sink.dat),
                    buffer.a_we.eq(1),

                    # Advance offset
                    NextValue(current_offset, current_offset + 1),
                    NextValue(cpl_bytes_received, cpl_bytes_received + bytes_per_beat),

                    If(sink.last & sink.end,
                        # This request complete
                        NextValue(current_addr, current_addr + tlp_len_bytes),
                        NextValue(remaining_len, remaining_len - tlp_len_bytes),

                        If((remaining_len - tlp_len_bytes) == 0,
                            NextValue(self.status, 0b00),  # Success
                            NextState("COMPLETE"),
                        ).Else(
                            NextState("SETUP"),
                        ),
                    ),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # WRITE PATH: Load data from buffer, issue Memory Write
        # ---------------------------------------------------------------------

        fsm.act("LOAD_DATA",
            self.busy.eq(1),

            # Read from buffer
            buffer.a_adr.eq(current_offset + beat_count),
            buffer.a_re.eq(1),

            NextState("ISSUE_WR"),
        )

        # Determine if this is first/last beat of TLP
        is_first_beat = Signal()
        is_last_beat  = Signal()
        beats_per_tlp = Signal(8)

        self.comb += [
            beats_per_tlp.eq((tlp_len_dwords + 1) >> 1),  # DWORDs / 2 for 64-bit
            is_first_beat.eq(beat_count == 0),
            is_last_beat.eq(beat_count == (beats_per_tlp - 1)),
        ]

        fsm.act("ISSUE_WR",
            self.busy.eq(1),

            source.valid.eq(1),
            source.first.eq(is_first_beat),
            source.last.eq(is_last_beat),
            source.we.eq(1),  # Write request
            source.adr.eq(effective_addr),  # Use ATC translation if available
            source.len.eq(tlp_len_dwords),
            source.dat.eq(buffer.a_dat_r),

            If(source.ready,
                NextValue(beat_count, beat_count + 1),

                If(is_last_beat,
                    # TLP complete
                    NextValue(current_addr, current_addr + tlp_len_bytes),
                    NextValue(current_offset, current_offset + beats_per_tlp),
                    NextValue(remaining_len, remaining_len - tlp_len_bytes),

                    If((remaining_len - tlp_len_bytes) == 0,
                        NextValue(self.status, 0b00),  # Success
                        NextState("COMPLETE"),
                    ).Else(
                        NextState("SETUP"),
                    ),
                ).Else(
                    # More beats in this TLP
                    NextState("LOAD_DATA"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # COMPLETE State: Signal completion
        # ---------------------------------------------------------------------
        # The PASID injector is self-latching (captures PASID on first beat),
        # so the engine can transition to IDLE immediately after signaling status.

        fsm.act("COMPLETE",
            self.busy.eq(0),
            self.status_we.eq(1),
            NextState("IDLE"),
        )

        # Tie off unused completion sink when not waiting for completions
        # (In WAIT_CPL state, ready=1 is set explicitly)
