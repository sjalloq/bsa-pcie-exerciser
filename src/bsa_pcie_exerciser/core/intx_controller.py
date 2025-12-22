#
# BSA PCIe Exerciser - Legacy INTx Controller
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Handles legacy INTx interrupt assertion/deassertion via Xilinx 7-series
# PCIe IP configuration interface using a stream endpoint with proper CDC.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import intx_layout


class INTxController(LiteXModule):
    """
    Legacy INTx interrupt controller for Xilinx 7-series PCIe.

    Manages the cfg_interrupt handshake protocol for asserting and
    deasserting legacy INTx interrupts. Follows the same pattern as
    LitePCIeMSI - owns a source endpoint that the user connects to
    the PHY's intx sink.

    Usage:
        self.intx_ctrl = INTxController()
        self.comb += self.intx_ctrl.source.connect(self.pcie_phy.intx)
        self.comb += self.intx_ctrl.intx_assert.eq(self.bsa_regs.intx_assert)

    Xilinx 7-series PCIe INTx Protocol:
    - To change interrupt state, pulse cfg_interrupt=1
    - cfg_interrupt_assert indicates desired state (1=assert, 0=deassert)
    - Wait for cfg_interrupt_rdy before next operation

    Attributes
    ----------
    source : stream.Endpoint
        INTx stream source to connect to PHY (phy.intx).
        Layout: [("level", 1)]

    intx_assert : Signal, in
        Desired INTx state from BSARegisters. When this changes,
        the controller automatically initiates a handshake to
        update the interrupt state.

    intx_pending : Signal, out
        Current interrupt assertion state (for status reporting).

    busy : Signal, out
        Controller is processing a state change request.
    """

    def __init__(self):
        # Stream source (connect to phy.intx)
        self.source = stream.Endpoint(intx_layout())

        # Control interface (directly connected to CSR bit)
        self.intx_assert = Signal()

        # Status interface
        self.intx_pending = Signal()  # Current asserted state
        self.busy = Signal()          # Transaction in progress

        # # #

        # Track current acknowledged state (updated after PHY handshake completes)
        current_state = Signal(reset=0)
        self.comb += self.intx_pending.eq(current_state)

        # Detect state change request
        state_change = Signal()
        self.comb += state_change.eq(self.intx_assert != current_state)

        # Latch target state on state change detection
        # This prevents issues if intx_assert changes during handshake
        target_state = Signal()

        # FSM for interrupt handshake
        self.fsm = fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            If(state_change,
                NextValue(target_state, self.intx_assert),
                NextState("REQUEST"),
            ),
        )

        fsm.act("REQUEST",
            # Drive stream interface
            self.source.valid.eq(1),
            self.source.level.eq(target_state),
            If(self.source.ready,
                # Handshake complete
                NextValue(current_state, target_state),
                NextState("IDLE"),
            ),
        )

        # Unused currently
        self.comb += self.busy.eq(fsm.ongoing("REQUEST"))
