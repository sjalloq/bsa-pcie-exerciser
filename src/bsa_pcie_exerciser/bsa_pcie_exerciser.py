#!/usr/bin/env python3
#
# BSA Exerciser - Minimal Top Level
#
# Copyright (c) 2024
# SPDX-License-Identifier: BSD-2-Clause
#
# Phase 1: Basic PCIe endpoint with CSR access
# - Uses standard LitePCIeEndpoint (single BAR0)
# - Verifies platform, CRG, PCIe link
# - Simple test CSRs accessible from host
#
# Future phases will add:
# - Multi-BAR support (requires PHY/depacketizer mods)
# - BSA DMA engine with attribute control
# - MSI-X with 2048 vectors
#

import os
import sys
import argparse

from migen import *
from litex.gen import *

from litex.soc.cores.clock import S7PLL
from litex.soc.interconnect.csr import *
from litex.soc.integration.soc_core import SoCMini
from litex.soc.integration.builder import Builder

from litepcie.phy.s7pciephy import S7PCIEPHY
from litepcie.core import LitePCIeEndpoint, LitePCIeMSI
from litepcie.frontend.wishbone import LitePCIeWishboneBridge

from .platform.spec_a7_platform import Platform


# =============================================================================
# Clock Reset Generator
# =============================================================================

class _CRG(LiteXModule):
    """
    Clock Reset Generator for SPEC-A7.
    
    Uses 125MHz oscillator on board, generates system clock.
    PCIe clock comes from PHY (100MHz refclk from PCIe connector).
    """
    def __init__(self, platform, sys_clk_freq):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()
        
        # 125MHz oscillator on SPEC-A7
        # clk125m_oe must be asserted to enable the oscillator
        clk125m_oe = platform.request("clk125m_oe")
        clk125m    = platform.request("clk125m")
        self.comb += clk125m_oe.eq(1)
        
        # PLL: 125MHz -> sys_clk_freq
        self.pll = pll = S7PLL(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk125m, 125e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq, margin=0)
        
        # False path between sys_clk and PLL input
        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin)


# =============================================================================
# Test CSR Module
# =============================================================================

class TestCSRs(LiteXModule):
    """
    Simple test CSRs to verify PCIe BAR0 access.
    """
    def __init__(self):
        # Scratch register - read/write
        self.scratch = CSRStorage(32, reset=0xDEADBEEF,
            description="Scratch register for testing")
        
        # ID register - read-only
        self.id = CSRStatus(32, reset=0xB5A00001,
            description="BSA Exerciser ID")
        
        # Counter - read-only, increments each clock
        self.counter = CSRStatus(32,
            description="Free-running counter")
        
        counter = Signal(32)
        self.sync += counter.eq(counter + 1)
        self.comb += self.counter.status.eq(counter)
        
        # LED control
        self.leds = CSRStorage(4, reset=0,
            description="LED control")


# =============================================================================
# BSA Exerciser SoC - Phase 1 (Minimal)
# =============================================================================

class BSAExerciserSoC(SoCMini):
    """
    Minimal BSA Exerciser SoC for Phase 1 testing.
    
    Features:
    - PCIe Gen2 x1 endpoint
    - BAR0 mapped to CSR space
    - Test CSRs for verifying access
    - No DMA yet (Phase 2)
    - No MSI-X yet (Phase 2)
    
    This validates:
    - SPEC-A7 platform definition
    - CRG and clocking
    - PCIe PHY and link training
    - BAR0 MMIO access from host
    """
    
    # Memory map
    mem_map = {
        "csr": 0x0000_0000,
    }
    
    def __init__(self, platform, sys_clk_freq=125e6):
        
        # SoCMini ---------------------------------------------------------------------------------
        SoCMini.__init__(self, platform,
            clk_freq      = sys_clk_freq,
            ident         = "BSA/SBSA PCIe Exerciser",
            ident_version = True,
        )
        
        # CRG -------------------------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)
        
        # PCIe PHY --------------------------------------------------------------------------------
        self.pcie_phy = S7PCIEPHY(platform, platform.request("pcie_x1"),
            data_width  = 64,
            bar0_size   = 0x1000,  # 4KB BAR0 for CSRs
            cd          = "sys",   # Use sys clock domain
        )
        
        # Add LTSSM tracer for debugging link training
        self.pcie_phy.add_ltssm_tracer()
        
        # PCIe <-> Sys clock domain crossing constraints
        platform.add_false_path_constraints(
            self.crg.cd_sys.clk, 
            self.pcie_phy.cd_pcie.clk
        )
        
        # PCIe Endpoint ---------------------------------------------------------------------------
        self.pcie_endpoint = LitePCIeEndpoint(self.pcie_phy,
            endianness           = "big",  # Match S7PCIEPHY
            max_pending_requests = 4,
        )
        
        # PCIe Wishbone Bridge (BAR0 -> Wishbone -> CSRs) -----------------------------------------
        self.pcie_bridge = LitePCIeWishboneBridge(self.pcie_endpoint,
            base_address = self.mem_map["csr"],
        )
        self.bus.add_master(master=self.pcie_bridge.wishbone)
        
        # Test CSRs -------------------------------------------------------------------------------
        self.test_csrs = TestCSRs()
        
        # LEDs (for visual feedback) --------------------------------------------------------------
        try:
            leds = platform.request_all("user_led")
            self.comb += leds.eq(self.test_csrs.leds.storage)
        except:
            pass  # LEDs not available on all platforms
        
        # MSI (minimal, for future use) -----------------------------------------------------------
        self.pcie_msi = LitePCIeMSI()
        self.comb += self.pcie_msi.source.connect(self.pcie_phy.msi)


# =============================================================================
# Build
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="BSA Exerciser")
    parser.add_argument("--build",         action="store_true", help="Build bitstream")
    parser.add_argument("--load",          action="store_true", help="Load bitstream via JTAG")
    parser.add_argument("--sys-clk-freq",  default=125e6, type=float, help="System clock frequency")
    parser.add_argument("--output-dir",    default="build/bsa_exerciser", help="Build output directory")
    args = parser.parse_args()
    
    # Create platform
    platform = Platform(variant="xc7a50t")
    
    # Create SoC
    soc = BSAExerciserSoC(platform,
        sys_clk_freq = int(args.sys_clk_freq),
    )
    
    # Build
    builder = Builder(soc, output_dir=args.output_dir)
    
    if args.build:
        builder.build()
    
    if args.load:
        prog = platform.create_programmer()
        prog.load_bitstream(os.path.join(args.output_dir, "gateware", "top.bit"))


if __name__ == "__main__":
    main()