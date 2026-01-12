#
# FT601 USB 3.0 Synchronous FIFO PHY
#
# Copyright (c) 2016 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2018-2019 Pierre-Olivier Vauboin <po@lambdaconcept.com>
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# FSM rewritten to match PCILeech pcileech_ft601.sv timing.
# Original pcie_screamer FSM had timing issues with the FT601.
#

from migen import *
from migen.fhdl.specials import Tristate

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.cores.usb_fifo import phy_description


class FT601Sync(LiteXModule):
    """
    FT601 USB 3.0 Synchronous FIFO PHY.

    Provides stream interfaces for USB communication:
    - sink: Data to send to USB host (sys clock domain)
    - source: Data received from USB host (sys clock domain)

    Internally handles clock domain crossing between sys and usb (FT601's 100MHz).

    The FSM uses PCILeech-style timing with proper wait states and cooldown
    periods for reliable FT601 communication.

    Pads interface (from platform.request("usb_fifo")):
    - clk: 100MHz clock from FT601
    - data[31:0]: Bidirectional data bus
    - be[3:0]: Byte enables
    - rxf_n: RX FIFO not empty (active low)
    - txe_n: TX FIFO not full (active low)
    - rd_n: Read strobe (active low)
    - wr_n: Write strobe (active low)
    - oe_n: Output enable (active low)
    - siwu_n: Send immediate / wake up (active low)
    - rst_n: Reset (active low)
    """

    def __init__(self, pads, dw=32, timeout=1024):
        # Clock domain crossing FIFOs
        # Read: USB -> sys
        read_fifo = ClockDomainsRenamer({"write": "usb", "read": "sys"})(
            stream.AsyncFIFO(phy_description(dw), 128)
        )
        # Write: sys -> USB
        write_fifo = ClockDomainsRenamer({"write": "sys", "read": "usb"})(
            stream.AsyncFIFO(phy_description(dw), 128)
        )

        # Small buffer in USB domain for read timing
        read_buffer = ClockDomainsRenamer("usb")(
            stream.SyncFIFO(phy_description(dw), 4)
        )
        self.comb += read_buffer.source.connect(read_fifo.sink)

        self.read_fifo = read_fifo
        self.read_buffer = read_buffer
        self.write_fifo = write_fifo

        # Stream interfaces (sys clock domain)
        self.sink = write_fifo.sink      # TX: sys -> USB
        self.source = read_fifo.source   # RX: USB -> sys

        # ---------------------------------------------------------------------
        # State encoding (matching PCILeech exactly)
        # ---------------------------------------------------------------------
        S_IDLE         = 0x0
        S_RX_WAIT1     = 0x2
        S_RX_WAIT2     = 0x3
        S_RX_WAIT3     = 0x4
        S_RX_ACTIVE    = 0x5
        S_RX_COOLDOWN1 = 0x6
        S_RX_COOLDOWN2 = 0x7
        S_TX_WAIT1     = 0x8
        S_TX_WAIT2     = 0x9
        S_TX_ACTIVE    = 0xA
        S_TX_COOLDOWN1 = 0xB
        S_TX_COOLDOWN2 = 0xC

        state = Signal(4, reset=S_IDLE)

        # ---------------------------------------------------------------------
        # Tristate data bus
        # ---------------------------------------------------------------------

        data_w = Signal(dw)
        data_r = Signal(dw)

        oe = Signal(reset=1)  # Tristate control: 1 = FPGA drives, 0 = hi-Z

        self.specials += [
            Tristate(pads.data, data_w, oe, data_r),
            Tristate(pads.be, 0XF, oe)
        ]

        # ---------------------------------------------------------------------
        # Static signals
        # ---------------------------------------------------------------------
        self.comb += [
            pads.siwu_n.eq(1),    # No send immediate
            pads.rst_n.eq(1),     # Not in reset
        ]

        # ---------------------------------------------------------------------
        # RX Data Path
        # Write received data to read_buffer when valid
        # Both data and valid are registered to match PCILeech timing:
        # - Data captured on cycle N appears on dout at cycle N+1
        # - Valid at cycle N+1 reflects conditions from cycle N
        # This ensures data and valid are aligned correctly.
        # ---------------------------------------------------------------------

        data_r_reg = Signal(dw)
        rx_valid = Signal()

        self.sync.usb += [
            data_r_reg.eq(data_r),
            rx_valid.eq(~pads.rxf_n & (state == S_RX_ACTIVE)),
        ]

        self.comb += [
            read_buffer.sink.data.eq(data_r_reg),
            read_buffer.sink.valid.eq(rx_valid),
            # Note: We don't check ready - FT601 doesn't support backpressure
            # The read_buffer should be sized to handle bursts
        ]

        # ---------------------------------------------------------------------
        # TX Data Path - Registered output stage
        # Read from write_fifo and send to FT601 via registered output
        # This matches PCILeech timing where FT601_DATA_OUT[0] is registered
        # ---------------------------------------------------------------------
        # Forward signal - when we're actually sending data
        tx_active = Signal()
        self.comb += tx_active.eq(~pads.txe_n & (state == S_TX_ACTIVE))

        # Registered output data
        data_w_reg = Signal(dw)

        # Latch data during TX_WAIT2 (pre-fetch) and TX_ACTIVE (streaming)
        # This ensures data is stable before WR_N asserts in TX_WAIT2
        tx_latch = Signal()
        self.comb += tx_latch.eq(
            ((state == S_TX_WAIT2) & write_fifo.source.valid) |
            (tx_active & write_fifo.source.valid)
        )

        self.sync.usb += [
            If(tx_latch,
                data_w_reg.eq(write_fifo.source.data),
            ),
        ]

        # Connect registered data to tristate output
        self.comb += data_w.eq(data_w_reg)

        # Consume from FIFO when latching new data
        self.sync.usb += write_fifo.source.ready.eq(tx_latch)

        # ---------------------------------------------------------------------
        # Control Signals - exact PCILeech logic (active low outputs)
        # All control signals are registered for proper timing
        # ---------------------------------------------------------------------

        # OE (tristate control) - LOW during RX states to release bus
        # PCILeech: OE <= (rst || FT601_RXF_N || (not in RX OE states))
        in_rx_oe_states = Signal()
        self.comb += in_rx_oe_states.eq(
            (state == S_RX_ACTIVE) | (state == S_RX_WAIT3) |
            (state == S_RX_WAIT2) | (state == S_RX_COOLDOWN1) |
            (state == S_RX_COOLDOWN2)
        )
        self.sync.usb += oe.eq(pads.rxf_n | ~in_rx_oe_states)

        # FT601_OE_N - asserted (low) during RX_WAIT2, RX_WAIT3, RX_ACTIVE
        # Use intermediate signal with reset=1 to ensure inactive at startup
        in_rx_oe_n_states = Signal()
        self.comb += in_rx_oe_n_states.eq(
            (state == S_RX_ACTIVE) | (state == S_RX_WAIT3) | (state == S_RX_WAIT2)
        )
        oe_n_reg = Signal(reset=1)
        self.sync.usb += oe_n_reg.eq(pads.rxf_n | ~in_rx_oe_n_states)
        self.comb += pads.oe_n.eq(oe_n_reg)

        # FT601_RD_N - asserted (low) during RX_WAIT3, RX_ACTIVE
        # Use intermediate signal with reset=1 to ensure inactive at startup
        in_rx_rd_states = Signal()
        self.comb += in_rx_rd_states.eq(
            (state == S_RX_ACTIVE) | (state == S_RX_WAIT3)
        )
        rd_n_reg = Signal(reset=1)
        self.sync.usb += rd_n_reg.eq(pads.rxf_n | ~in_rx_rd_states)
        self.comb += pads.rd_n.eq(rd_n_reg)

        # FT601_WR_N - asserted (low) during TX_WAIT2 and TX_ACTIVE (when data available)
        # Use intermediate signal with reset=1 to ensure inactive at startup
        wr_condition = Signal()
        self.comb += wr_condition.eq(
            ~pads.txe_n & (
                ((state == S_TX_ACTIVE) & write_fifo.source.valid)
            )
        )
        wr_n_reg = Signal(reset=1)
        self.sync.usb += wr_n_reg.eq(~wr_condition)
        self.comb += pads.wr_n.eq(wr_n_reg)

        # ---------------------------------------------------------------------
        # State Machine - exact PCILeech transitions
        # RX is prioritized over TX when both are available
        # ---------------------------------------------------------------------
        self.sync.usb += [
            Case(state, {
                # IDLE: prioritize RX over TX
                S_IDLE: [
                    If(~pads.rxf_n,
                        state.eq(S_RX_WAIT1),
                    ).Elif(~pads.txe_n & write_fifo.source.valid,
                        state.eq(S_TX_WAIT1),
                    ),
                ],

                # RX path - 3 wait states before active
                S_RX_WAIT1: [
                    If(pads.rxf_n,
                        state.eq(S_RX_COOLDOWN1),
                    ).Else(
                        state.eq(S_RX_WAIT2),
                    ),
                ],
                S_RX_WAIT2: [
                    If(pads.rxf_n,
                        state.eq(S_RX_COOLDOWN1),
                    ).Else(
                        state.eq(S_RX_WAIT3),
                    ),
                ],
                S_RX_WAIT3: [
                    If(pads.rxf_n,
                        state.eq(S_RX_COOLDOWN1),
                    ).Else(
                        state.eq(S_RX_ACTIVE),
                    ),
                ],
                S_RX_ACTIVE: [
                    If(pads.rxf_n,
                        state.eq(S_RX_COOLDOWN1),
                    ),
                    # else stay in RX_ACTIVE
                ],
                S_RX_COOLDOWN1: [
                    state.eq(S_RX_COOLDOWN2),
                ],
                S_RX_COOLDOWN2: [
                    state.eq(S_IDLE),
                ],

                # TX path - 2 wait states before active
                S_TX_WAIT1: [
                    If(pads.txe_n,
                        state.eq(S_TX_COOLDOWN1),
                    ).Else(
                        state.eq(S_TX_WAIT2),
                    ),
                ],
                S_TX_WAIT2: [
                    If(pads.txe_n,
                        state.eq(S_TX_COOLDOWN1),
                    ).Else(
                        state.eq(S_TX_ACTIVE),
                    ),
                ],
                S_TX_ACTIVE: [
                    If(pads.txe_n | ~write_fifo.source.valid,
                        state.eq(S_TX_COOLDOWN1),
                    ),
                    # else stay in TX_ACTIVE
                ],
                S_TX_COOLDOWN1: [
                    state.eq(S_TX_COOLDOWN2),
                ],
                S_TX_COOLDOWN2: [
                    state.eq(S_IDLE),
                ],
            }),
        ]

        # Debug signals
        self.state = state
        self.oe = oe
        self.data_w = data_w
        self.data_r = data_r
