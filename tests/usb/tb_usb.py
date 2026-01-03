#!/usr/bin/env python3
#
# BSA PCIe Exerciser - USB Testbench
#
# Copyright (c) 2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
USB Testbench for SquirrelSoC.

Instantiates the real SquirrelSoC with:
- PHYStub for PCIe interface
- FT601Stub for USB interface
- Dual clock domains (sys @ 125MHz, usb @ 100MHz)

Exposes all necessary signals at top level for cocotb access.

Usage:
    python tb_usb.py  # Generates build/sim/tb_usb.v
"""

import os
import sys

from migen import *
from litex.gen import *

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.platform import TestPlatform
from tests.common.phy_stub import PHYStub
from tests.common.ft601_stub import FT601StubWithFIFO

from bsa_pcie_exerciser.gateware.soc import SquirrelSoC


# =============================================================================
# USB Test Platform
# =============================================================================

class USBTestPlatform(TestPlatform):
    """
    Extended test platform for USB testbench.

    Since we inject the USB PHY (FT601Stub), we don't need actual
    USB FIFO pads - the stub bypasses hardware pads entirely.
    """
    name = "usb_test_platform"


# =============================================================================
# USB Testbench Wrapper
# =============================================================================

class USBTestbench(LiteXModule):
    """
    Migen testbench wrapper for USB subsystem testing.

    Instantiates the real SquirrelSoC with:
    - PHYStub for PCIe interface
    - FT601StubWithFIFO for USB interface (with FIFOs for easier cocotb integration)

    Exposes:
    - PCIe PHY signals (for TLP injection/capture via PCIe path)
    - USB stub signals (for Etherbone and monitor testing via USB path)

    Clock domains:
    - cd_sys: 125MHz system clock (driven by cocotb)
    - cd_pcie: 125MHz PCIe clock (driven by cocotb, tied to sys)
    - cd_usb: 100MHz USB clock (driven by cocotb, async to sys)
    """

    def __init__(self, data_width=64):
        # =====================================================================
        # Clock Domains (driven by cocotb)
        # =====================================================================

        self.cd_sys = ClockDomain("sys")
        self.cd_pcie = ClockDomain("pcie")
        self.cd_usb = ClockDomain("usb")

        # =====================================================================
        # Platform and PHY Stubs
        # =====================================================================

        platform = USBTestPlatform()
        self.pcie_phy = PHYStub(data_width=data_width)
        self.usb_phy = FT601StubWithFIFO(dw=32, rx_depth=64, tx_depth=64)

        # =====================================================================
        # SoC Instantiation
        # =====================================================================

        self.soc = SquirrelSoC(
            platform=platform,
            pcie_phy=self.pcie_phy,
            usb_phy=self.usb_phy,
            simulation=True,
        )

        # =====================================================================
        # PCIe PHY Signal Exposure (same as IntegrationTestbench)
        # =====================================================================

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

        # INTx
        self.intx_asserted = Signal(name="intx_asserted")

        # Wire external signals to PCIe PHY stub
        self.comb += [
            # RX: testbench -> PHY source -> endpoint
            self.pcie_phy.source.valid.eq(self.phy_rx_valid),
            self.phy_rx_ready.eq(self.pcie_phy.source.ready),
            self.pcie_phy.source.first.eq(self.phy_rx_first),
            self.pcie_phy.source.last.eq(self.phy_rx_last),
            self.pcie_phy.source.dat.eq(self.phy_rx_dat),
            self.pcie_phy.source.be.eq(self.phy_rx_be),
            self.pcie_phy.source.bar_hit.eq(self.phy_rx_bar_hit),

            # TX: endpoint -> PHY sink -> testbench
            self.phy_tx_valid.eq(self.pcie_phy.sink.valid),
            self.pcie_phy.sink.ready.eq(self.phy_tx_ready),
            self.phy_tx_first.eq(self.pcie_phy.sink.first),
            self.phy_tx_last.eq(self.pcie_phy.sink.last),
            self.phy_tx_dat.eq(self.pcie_phy.sink.dat),
            self.phy_tx_be.eq(self.pcie_phy.sink.be),

            # INTx
            self.intx_asserted.eq(self.pcie_phy.intx_asserted),
        ]

        # =====================================================================
        # USB PHY Signal Exposure
        # =====================================================================

        # Host -> Device injection (cocotb drives these)
        self.usb_inject_valid = Signal(name="usb_inject_valid")
        self.usb_inject_ready = Signal(name="usb_inject_ready")
        self.usb_inject_data  = Signal(32, name="usb_inject_data")

        # Device -> Host capture (cocotb reads these)
        self.usb_capture_valid = Signal(name="usb_capture_valid")
        self.usb_capture_ready = Signal(name="usb_capture_ready")
        self.usb_capture_data  = Signal(32, name="usb_capture_data")

        # Backpressure control
        self.usb_tx_backpressure = Signal(name="usb_tx_backpressure")

        # FIFO status
        self.usb_rx_fifo_level = Signal(8, name="usb_rx_fifo_level")
        self.usb_tx_fifo_level = Signal(8, name="usb_tx_fifo_level")

        # Wire external signals to USB stub
        self.comb += [
            # Host -> Device
            self.usb_phy.inject_valid.eq(self.usb_inject_valid),
            self.usb_inject_ready.eq(self.usb_phy.inject_ready),
            self.usb_phy.inject_data.eq(self.usb_inject_data),

            # Device -> Host
            self.usb_capture_valid.eq(self.usb_phy.capture_valid),
            self.usb_phy.capture_ready.eq(self.usb_capture_ready),
            self.usb_capture_data.eq(self.usb_phy.capture_data),

            # Control
            self.usb_phy.tx_backpressure.eq(self.usb_tx_backpressure),

            # Status
            self.usb_rx_fifo_level.eq(self.usb_phy.rx_fifo_level),
            self.usb_tx_fifo_level.eq(self.usb_phy.tx_fifo_level),
        ]


# =============================================================================
# Verilog Generation
# =============================================================================

def generate_verilog():
    """Generate Verilog for cocotb simulation."""
    from migen.fhdl.verilog import convert

    testbench = USBTestbench()

    ios = {
        # Clock domains
        testbench.cd_sys.clk,
        testbench.cd_sys.rst,
        testbench.cd_pcie.clk,
        testbench.cd_pcie.rst,
        testbench.cd_usb.clk,
        testbench.cd_usb.rst,

        # PCIe RX path
        testbench.phy_rx_valid, testbench.phy_rx_ready,
        testbench.phy_rx_first, testbench.phy_rx_last,
        testbench.phy_rx_dat, testbench.phy_rx_be, testbench.phy_rx_bar_hit,

        # PCIe TX path
        testbench.phy_tx_valid, testbench.phy_tx_ready,
        testbench.phy_tx_first, testbench.phy_tx_last,
        testbench.phy_tx_dat, testbench.phy_tx_be,

        # INTx
        testbench.intx_asserted,

        # USB inject (Host -> Device)
        testbench.usb_inject_valid, testbench.usb_inject_ready, testbench.usb_inject_data,

        # USB capture (Device -> Host)
        testbench.usb_capture_valid, testbench.usb_capture_ready, testbench.usb_capture_data,

        # USB control/status
        testbench.usb_tx_backpressure,
        testbench.usb_rx_fifo_level, testbench.usb_tx_fifo_level,
    }

    output = convert(testbench, ios=ios, name="tb_usb")

    # Write Verilog to build directory
    build_dir = "build/sim"
    os.makedirs(build_dir, exist_ok=True)

    orig_dir = os.getcwd()
    os.chdir(build_dir)
    output.write("tb_usb.v")
    os.chdir(orig_dir)

    print(f"Generated {build_dir}/tb_usb.v")


if __name__ == "__main__":
    generate_verilog()
