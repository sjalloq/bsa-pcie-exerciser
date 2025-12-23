#!/usr/bin/env python3
#
# BSA PCIe Exerciser - Integration Testbench
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Migen testbench wrapper for full SoC integration tests.

Instantiates the REAL BSAExerciserSoC with PHY/Platform stubs
and exposes PHY signals at the top level for cocotb to inject/capture TLPs.

Usage:
    python tb_integration.py  # Generates build/sim/tb_integration.v
"""

import os
import sys

from migen import *
from litex.gen import *

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.platform import TestPlatform
from tests.common.phy_stub import PHYStub

from bsa_pcie_exerciser.bsa_pcie_exerciser import BSAExerciserSoC


# =============================================================================
# Integration Testbench Wrapper
# =============================================================================

class IntegrationTestbench(LiteXModule):
    """
    Migen testbench wrapper that instantiates the real BSAExerciserSoC with stubs.

    Exposes PHY signals at the top level for cocotb to inject/capture TLPs.
    """

    def __init__(self, data_width=64):
        # Clock domains - driven by cocotb
        self.cd_sys = ClockDomain("sys")
        self.cd_pcie = ClockDomain("pcie")

        # Create platform and PHY stubs
        platform = TestPlatform()
        self.phy = PHYStub(data_width=data_width)

        # Create the REAL BSAExerciserSoC with stubs
        self.soc = BSAExerciserSoC(
            platform   = platform,
            pcie_phy   = self.phy,
            simulation = True,
        )

        # Expose PHY signals at top level for cocotb
        # RX path (testbench -> DUT)
        self.phy_rx_valid   = Signal(name="phy_rx_valid")
        self.phy_rx_ready   = Signal(name="phy_rx_ready")
        self.phy_rx_first   = Signal(name="phy_rx_first")
        self.phy_rx_last    = Signal(name="phy_rx_last")
        self.phy_rx_dat     = Signal(data_width, name="phy_rx_dat")
        self.phy_rx_be      = Signal(data_width // 8, name="phy_rx_be")
        self.phy_rx_bar_hit = Signal(6, name="phy_rx_bar_hit")

        # TX path (DUT -> testbench)
        self.phy_tx_valid   = Signal(name="phy_tx_valid")
        self.phy_tx_ready   = Signal(name="phy_tx_ready")
        self.phy_tx_first   = Signal(name="phy_tx_first")
        self.phy_tx_last    = Signal(name="phy_tx_last")
        self.phy_tx_dat     = Signal(data_width, name="phy_tx_dat")
        self.phy_tx_be      = Signal(data_width // 8, name="phy_tx_be")

        # INTx path (DUT -> testbench)
        # PHY stub always accepts (ready=1), so we just expose the latched state
        self.intx_asserted = Signal(name="intx_asserted")  # Latched state in PHY

        # Wire external signals to PHY stub
        self.comb += [
            # RX: testbench -> PHY source -> endpoint (via depacketizer)
            self.phy.source.valid.eq(self.phy_rx_valid),
            self.phy_rx_ready.eq(self.phy.source.ready),
            self.phy.source.first.eq(self.phy_rx_first),
            self.phy.source.last.eq(self.phy_rx_last),
            self.phy.source.dat.eq(self.phy_rx_dat),
            self.phy.source.be.eq(self.phy_rx_be),
            self.phy.source.bar_hit.eq(self.phy_rx_bar_hit),

            # TX: endpoint (via packetizer) -> PHY sink -> testbench
            self.phy_tx_valid.eq(self.phy.sink.valid),
            self.phy.sink.ready.eq(self.phy_tx_ready),
            self.phy_tx_first.eq(self.phy.sink.first),
            self.phy_tx_last.eq(self.phy.sink.last),
            self.phy_tx_dat.eq(self.phy.sink.dat),
            self.phy_tx_be.eq(self.phy.sink.be),

            # INTx: PHY latched state -> testbench
            self.intx_asserted.eq(self.phy.intx_asserted),
        ]


# =============================================================================
# Verilog Generation
# =============================================================================

def generate_verilog():
    """Generate Verilog for cocotb simulation."""
    from migen.fhdl.verilog import convert

    testbench = IntegrationTestbench()

    ios = {
        testbench.cd_sys.clk,
        testbench.cd_sys.rst,
        testbench.cd_pcie.clk,
        testbench.cd_pcie.rst,
        # RX path
        testbench.phy_rx_valid, testbench.phy_rx_ready,
        testbench.phy_rx_first, testbench.phy_rx_last,
        testbench.phy_rx_dat, testbench.phy_rx_be, testbench.phy_rx_bar_hit,
        # TX path
        testbench.phy_tx_valid, testbench.phy_tx_ready,
        testbench.phy_tx_first, testbench.phy_tx_last,
        testbench.phy_tx_dat, testbench.phy_tx_be,
        # INTx
        testbench.intx_asserted,
    }

    output = convert(testbench, ios=ios, name="tb_integration")

    # Write Verilog to build directory using output.write() which
    # properly handles memory initialization files as separate .init files
    build_dir = "build/sim"
    os.makedirs(build_dir, exist_ok=True)

    orig_dir = os.getcwd()
    os.chdir(build_dir)
    output.write("tb_integration.v")
    os.chdir(orig_dir)

    print(f"Generated {build_dir}/tb_integration.v")


if __name__ == "__main__":
    generate_verilog()
