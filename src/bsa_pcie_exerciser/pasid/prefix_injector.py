#
# BSA PCIe Exerciser - PASID TLP Prefix Injector
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Inserts PASID TLP Prefix (E2E format 0x91) into outbound TLP stream.
# Sits between endpoint packetizer output and PHY sink.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import phy_layout


class PASIDPrefixInjector(LiteXModule):
    """
    PASID TLP Prefix Injector for 64-bit data width.

    Inserts an E2E PASID TLP Prefix (format 0x91) when pasid_en is asserted.
    The prefix is a single 32-bit DWORD that precedes the TLP header.

    E2E PASID Prefix format:
        Bits [31:24]: 0x91 (E2E TLP Prefix Type for PASID)
        Bit  [21]:    PMR (Privileged Mode Requested)
        Bit  [20]:    Execute Requested
        Bits [19:0]:  PASID Value (20 bits)

    For 64-bit datapath, the shifting works as:
        Without prefix: [HDR0|HDR1][HDR2|HDR3][DATA...]
        With prefix:    [PREFIX|HDR0][HDR1|HDR2][HDR3|DATA0][DATA...]

    Stream-Based Design:
        PASID signals travel with the TLP data through phy_layout fields
        (pasid_en, pasid_val, privileged, execute). No external signals needed.
        When the first beat arrives, we capture these fields and buffer the
        beat. In the next cycle (DECIDE state), we use the captured values to
        decide whether to add a prefix. This eliminates timing races entirely.

    Attributes
    ----------
    sink : stream.Endpoint
        Input from packetizer (phy_layout with PASID fields).

    source : stream.Endpoint
        Output to PHY (phy_layout). PASID fields consumed here (not passed to PHY).
    """

    def __init__(self, data_width=64):
        assert data_width == 64, "Only 64-bit data width supported currently"

        # Stream interfaces
        self.sink   = sink   = stream.Endpoint(phy_layout(data_width))
        self.source = source = stream.Endpoint(phy_layout(data_width))

        # # #

        # Build PASID prefix DWORD (E2E format 0x91) from stream signals
        # Per PCIe spec: Fmt=100b (TLP Prefix), Type=10001b (E2E PASID)
        # Combined: (0b100 << 5) | 0b10001 = 0x91
        prefix_dword = Signal(32)
        self.comb += prefix_dword.eq(
            (0x91 << 24) |                      # E2E PASID prefix type
            (sink.privileged << 21) |           # PMR from stream
            (sink.execute << 20) |              # Execute from stream
            (sink.pasid_val & 0xFFFFF)          # PASID value from stream
        )

        # =====================================================================
        # Self-Latching: Capture PASID signals on first beat
        # =====================================================================
        # When we see the first beat of a TLP, we capture the PASID signals
        # from the stream and buffer the beat. In the next cycle (DECIDE state),
        # we use the captured values to decide whether to add a prefix.

        # Captured PASID signals (sampled on first beat)
        captured_pasid_en = Signal()
        captured_prefix   = Signal(32)

        # Buffered first beat (held while we decide)
        buffered_first_dat  = Signal(64, reset_less=True)
        buffered_first_be   = Signal(8, reset_less=True)
        buffered_first_last = Signal()

        # Buffered DWORD from previous beat (for shifting in multi-beat TLPs)
        buffered_dword = Signal(32, reset_less=True)
        buffered_be    = Signal(4, reset_less=True)
        buffered_valid = Signal()

        # State machine
        self.fsm = fsm = FSM(reset_state="IDLE")

        # IDLE: Wait for first beat, capture PASID signals and buffer beat
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid & sink.first,
                # Capture PASID signals from stream for stable use in DECIDE state
                NextValue(captured_pasid_en, sink.pasid_en),
                NextValue(captured_prefix, prefix_dword),

                # Buffer the first beat
                NextValue(buffered_first_dat, sink.dat),
                NextValue(buffered_first_be, sink.be),
                NextValue(buffered_first_last, sink.last),

                NextState("DECIDE"),
            ),
        )

        # DECIDE: Use captured PASID signals to decide path (race-free)
        fsm.act("DECIDE",
            sink.ready.eq(0),  # Hold - don't accept new data yet

            If(captured_pasid_en,
                # With prefix: output [PREFIX | HDR0]
                source.valid.eq(1),
                source.first.eq(1),
                source.last.eq(0),  # Never last on first beat with prefix
                source.dat[0:32].eq(captured_prefix),
                source.dat[32:64].eq(buffered_first_dat[0:32]),
                source.be[0:4].eq(0xF),
                source.be[4:8].eq(0xF),

                # Buffer upper DWORD for next beat
                NextValue(buffered_dword, buffered_first_dat[32:64]),
                NextValue(buffered_be, buffered_first_be[4:8]),
                NextValue(buffered_valid, 1),

                If(source.ready,
                    If(buffered_first_last,
                        # Original TLP was 1 beat, need to flush buffer
                        NextState("FLUSH"),
                    ).Else(
                        NextState("SHIFT"),
                    ),
                ),
            ).Else(
                # No prefix: output buffered first beat directly
                source.valid.eq(1),
                source.first.eq(1),
                source.last.eq(buffered_first_last),
                source.dat.eq(buffered_first_dat),
                source.be.eq(buffered_first_be),

                If(source.ready,
                    If(buffered_first_last,
                        # Single-beat TLP without prefix, done
                        NextState("IDLE"),
                    ).Else(
                        # Multi-beat TLP without prefix
                        NextState("PASSTHROUGH"),
                    ),
                ),
            ),
        )

        # PASSTHROUGH: Pass through TLP beats when PASID prefix is not needed
        fsm.act("PASSTHROUGH",
            sink.connect(source),
            If(sink.valid & sink.last & source.ready,
                NextState("IDLE"),
            ),
        )

        # SHIFT: Continue shifting DWORDs by one position
        fsm.act("SHIFT",
            sink.ready.eq(source.ready),
            source.valid.eq(sink.valid),
            source.first.eq(0),
            source.last.eq(0),

            # Output: [buffered | lower DWORD of current]
            source.dat[0:32].eq(buffered_dword),
            source.dat[32:64].eq(sink.dat[0:32]),
            source.be[0:4].eq(buffered_be),
            source.be[4:8].eq(sink.be[0:4]),

            If(sink.valid & source.ready,
                # Buffer upper DWORD
                NextValue(buffered_dword, sink.dat[32:64]),
                NextValue(buffered_be, sink.be[4:8]),

                If(sink.last,
                    # Check if we need to flush the buffered DWORD
                    If(sink.be[4:8] != 0,
                        # Upper DWORD has valid data, need flush beat
                        NextState("FLUSH"),
                    ).Else(
                        # Upper DWORD is empty, this is the last beat
                        source.last.eq(1),
                        NextValue(buffered_valid, 0),
                        NextState("IDLE"),
                    ),
                ),
            ),
        )

        # FLUSH: Output final partial beat (buffered DWORD only)
        fsm.act("FLUSH",
            sink.ready.eq(0),
            source.valid.eq(buffered_valid),
            source.first.eq(0),
            source.last.eq(1),

            # Output buffered DWORD in lower position, upper is padding
            source.dat[0:32].eq(buffered_dword),
            source.dat[32:64].eq(0),
            source.be[0:4].eq(buffered_be),
            source.be[4:8].eq(0),  # Upper DWORD not valid

            If(source.ready,
                NextValue(buffered_valid, 0),
                NextState("IDLE"),
            ),
        )
