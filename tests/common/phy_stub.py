#
# BSA PCIe Exerciser - PHY Stub for Simulation
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Drop-in replacement for S7PCIEPHY in simulation.

Provides the same interface but no Xilinx primitives, allowing the full
SoC to be elaborated and simulated with Verilator or other simulators.
"""

from migen import Signal, ClockDomain, If
from litex.gen import LiteXModule
from litex.soc.interconnect import stream

from litepcie.common import phy_layout, msi_layout, intx_layout, get_bar_mask


class PHYStub(LiteXModule):
    """
    Drop-in replacement for S7PCIEPHY in simulation.

    Provides the same interface but no Xilinx primitives.
    """
    endianness = "big"
    qword_aligned = False

    def __init__(self, data_width=64):
        self.data_width = data_width

        # Stream interfaces (directly exposed)
        self.sink   = stream.Endpoint(phy_layout(data_width))  # TX from DUT
        self.source = stream.Endpoint(phy_layout(data_width))  # RX to DUT
        self.msi    = stream.Endpoint(msi_layout())
        self.intx   = stream.Endpoint(intx_layout())

        # Configuration signals (directly settable)
        self.id               = Signal(16, reset=0x0100)  # Bus 0, Dev 1, Fn 0
        self.bar0_size        = 0x1000   # 4KB
        self.bar0_mask        = get_bar_mask(0x1000)  # 0xFFFFF000 - upper bits to mask off
        self.max_request_size = Signal(16, reset=512)
        self.max_payload_size = Signal(16, reset=256)

        # Clock domain stub
        self.cd_pcie = ClockDomain("pcie", reset_less=True)

        # Config storage (for update_config)
        self.config = {}

        # MSI/INTx always ready in stub
        self.comb += [
            self.msi.ready.eq(1),
            self.intx.ready.eq(1),
        ]

        # Latch INTx level on handshake (mimics real PHY behavior)
        # Real PHY maintains interrupt state after handshake completes
        self.intx_asserted = Signal()
        self.sync += [
            If(self.intx.valid & self.intx.ready,
                self.intx_asserted.eq(self.intx.level),
            ),
        ]

    def update_config(self, config):
        """Accept config dict (ignored in stub)."""
        self.config.update(config)

    def add_ltssm_tracer(self):
        """No LTSSM in simulation."""
        pass
