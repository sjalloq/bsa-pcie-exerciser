#!/usr/bin/env python3
#
# BSA PCIe Exerciser - Top Level
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Uses modified LitePCIe fork with bar_hit extraction and attr passthrough.
#

import os
import argparse

from migen import *
from litex.gen import *

from litex.soc.cores.clock import S7PLL
from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.soc_core import SoCMini
from litex.soc.integration.builder import Builder

from litepcie.phy.s7pciephy import S7PCIEPHY
from litepcie.core import LitePCIeMSI
from litepcie.core.crossbar import LitePCIeCrossbar
from litepcie.core.endpoint import LitePCIeEndpoint
from litepcie.tlp.depacketizer import LitePCIeTLPDepacketizer
from litepcie.tlp.packetizer import LitePCIeTLPPacketizer
from litepcie.frontend.wishbone import LitePCIeWishboneBridge

from bsa_pcie_exerciser.core import (
    LitePCIeBARDispatcher,
    LitePCIeCompletionArbiter,
    LitePCIeMasterArbiter,
    LitePCIeStubBARHandler,
    LitePCIeMultiBAREndpoint,
    BSARegisters,
)

from bsa_pcie_exerciser.dma import (
    BSADMABuffer,
    BSADMABufferHandler,
    BSADMAEngine,
)

from bsa_pcie_exerciser.monitor import TransactionMonitor

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
        clk125m_oe = platform.request("clk125m_oe")
        clk125m    = platform.request("clk125m")
        self.comb += clk125m_oe.eq(1)

        # PLL: 125MHz -> sys_clk_freq
        self.pll = pll = S7PLL(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk125m, 125e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq, margin=0)

        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin)


# =============================================================================
# BSA Exerciser SoC - Phase 2 (Multi-BAR)
# =============================================================================

class BSAExerciserSoC(SoCMini):
    """
    BSA Exerciser SoC with multi-BAR configuration.

    Phase 2 Features:
    - Multi-BAR IP configuration (BAR0, BAR1, BAR2, BAR5)
    - BAR0 active with CSR access
    - BAR1/2/5 configured but handlers TBD
    - Prepared for MSI-X (2048 vectors)
    """

    mem_map = {
        "csr": 0x8000_0000,
    }

    def __init__(self, platform, sys_clk_freq=125e6):

        # SoCMini -----------------------------------------------------------------
        SoCMini.__init__(self, platform,
            clk_freq      = sys_clk_freq,
            ident         = "BSA PCIe Exerciser Phase 2",
            ident_version = True,
        )

        # CRG ---------------------------------------------------------------------
        self.crg = _CRG(platform, sys_clk_freq)

        # PCIe PHY ----------------------------------------------------------------
        self.pcie_phy = S7PCIEPHY(platform, platform.request("pcie_x1"),
            data_width = 64,
            cd         = "sys",
        )

        # Multi-BAR Configuration -------------------------------------------------
        # Configure the Xilinx PCIe IP for multiple BARs and Device ID
        self.pcie_phy.update_config({
            # Device Identification (ARM BSA Exerciser)
            "Vendor_ID"          : "13B5",   # ARM Ltd.
            "Device_ID"          : "ED01",   # BSA Exerciser
            "Revision_ID"        : "01",
            "Subsystem_Vendor_ID": "13B5",
            "Subsystem_ID"       : "ED01",

            # BAR0: CSRs (4KB)
            "Bar0_Enabled"      : True,
            "Bar0_Scale"        : "Kilobytes",
            "Bar0_Size"         : 4,
            "Bar0_Type"         : "Memory",
            "Bar0_Prefetchable" : False,

            # BAR1: DMA Buffer (16KB) - for Phase 4
            "Bar1_Enabled"      : True,
            "Bar1_Scale"        : "Kilobytes",
            "Bar1_Size"         : 16,
            "Bar1_Type"         : "Memory",
            "Bar1_Prefetchable" : False,

            # BAR2: MSI-X Table (32KB for 2048 vectors) - for Phase 3
            "Bar2_Enabled"      : True,
            "Bar2_Scale"        : "Kilobytes",
            "Bar2_Size"         : 32,
            "Bar2_Type"         : "Memory",
            "Bar2_Prefetchable" : False,  # MSI-X must be non-prefetchable

            # BAR3/4: Disabled
            "Bar3_Enabled"      : False,
            "Bar4_Enabled"      : False,

            # BAR5: MSI-X PBA (4KB) - for Phase 3
            "Bar5_Enabled"      : True,
            "Bar5_Scale"        : "Kilobytes",
            "Bar5_Size"         : 4,
            "Bar5_Type"         : "Memory",
            "Bar5_Prefetchable" : False,  # MSI-X must be non-prefetchable

            # MSI-X Configuration (2048 vectors)
            "MSI_Enabled"       : False,  # Disable legacy MSI
            "MSIx_Enabled"      : True,
            "MSIx_Table_Size"   : "7FF",  # 2048 vectors (N-1 encoding, hex)
            "MSIx_Table_BIR"    : "BAR_2",
            "MSIx_Table_Offset" : "0",
            "MSIx_PBA_BIR"      : "BAR_5",
            "MSIx_PBA_Offset"   : "0",
        })

        # LTSSM Tracer for link debugging
        self.pcie_phy.add_ltssm_tracer()

        # Clock domain crossing constraints
        platform.add_false_path_constraints(
            self.crg.cd_sys.clk,
            self.pcie_phy.cd_pcie.clk
        )

        # DMA Buffer and Handler --------------------------------------------------
        # Create before endpoint so we can pass handler to it
        self.dma_buffer = BSADMABuffer(
            size       = 16*1024,  # 16KB buffer
            data_width = self.pcie_phy.data_width,
        )

        self.dma_handler = BSADMABufferHandler(
            phy        = self.pcie_phy,
            buffer     = self.dma_buffer,
            data_width = self.pcie_phy.data_width,
        )

        # PCIe Endpoint -----------------------------------------------------------
        self.pcie_endpoint = LitePCIeMultiBAREndpoint(self.pcie_phy,
            endianness           = "big",
            max_pending_requests = 4,
            bar_enables  = {0: True, 1: False, 2: False, 3: False, 4: False, 5: False},
            bar_handlers = {1: self.dma_handler},  # BAR1 handled by DMA buffer handler
        )

        # BSA Registers -----------------------------------------------------------
        # Full ARM BSA Exerciser register set with explicit address mapping
        self.bsa_regs = BSARegisters()
        self.bus.add_slave(
            name="bsa_regs",
            slave=self.bsa_regs.bus,
            region=SoCRegion(origin=0x0, size=0x1000, cached=False),
        )

        # Wishbone Bridge (BAR0 -> BSA Registers) ---------------------------------
        # Connect PCIe Wishbone bridge directly to BSA registers
        # (bypasses SoC bus to avoid conflict with auto-generated CSR bridge)
        self.pcie_bridge = LitePCIeWishboneBridge(self.pcie_endpoint,
            base_address = self.mem_map["csr"],
        )
        self.bus.add_master("pcie", master=self.pcie_bridge.wishbone)


        # DMA Engine --------------------------------------------------------------
        self.dma_engine = BSADMAEngine(
            phy              = self.pcie_phy,
            buffer           = self.dma_buffer,
            data_width       = self.pcie_phy.data_width,
            max_request_size = 128,
        )

        # Connect DMA engine control signals to BSA registers
        self.comb += [
            self.dma_engine.trigger.eq(self.bsa_regs.dma_trigger),
            self.dma_engine.direction.eq(self.bsa_regs.dma_direction),
            self.dma_engine.no_snoop.eq(self.bsa_regs.dma_no_snoop),
            self.dma_engine.addr_type.eq(self.bsa_regs.dma_addr_type),
            self.dma_engine.bus_addr.eq(self.bsa_regs.dma_bus_addr),
            self.dma_engine.length.eq(self.bsa_regs.dma_len),
            self.dma_engine.offset.eq(self.bsa_regs.dma_offset),
        ]

        # Connect DMA engine status signals to BSA registers
        self.comb += [
            self.bsa_regs.dma_busy.eq(self.dma_engine.busy),
            self.bsa_regs.dma_status.eq(self.dma_engine.status),
            self.bsa_regs.dma_status_we.eq(self.dma_engine.status_we),
        ]

        # Connect DMA engine to master port for TLP requests
        # Note: LitePCIeMasterPort swaps sink/source internally:
        #   dma_port.source = internal port's sink (request_layout)
        #   dma_port.sink   = internal port's source (completion_layout)
        dma_port = self.pcie_endpoint.crossbar.get_master_port()
        self.comb += [
            self.dma_engine.source.connect(dma_port.source),  # request -> request
            dma_port.sink.connect(self.dma_engine.sink),      # completion -> completion
        ]

        # Transaction Monitor -------------------------------------------------------
        self.txn_monitor = TransactionMonitor(
            data_width = self.pcie_phy.data_width,
            fifo_depth = 32,  # BSA spec maximum
        )

        # Tap into the request stream from depacketizer
        req_source = self.pcie_endpoint.req_source
        self.comb += [
            self.txn_monitor.tap_valid.eq(req_source.valid & req_source.ready),
            self.txn_monitor.tap_first.eq(req_source.first),
            self.txn_monitor.tap_last.eq(req_source.last),
            self.txn_monitor.tap_we.eq(req_source.we),
            self.txn_monitor.tap_adr.eq(req_source.adr),
            self.txn_monitor.tap_len.eq(req_source.len),
            self.txn_monitor.tap_dat.eq(req_source.dat),
            self.txn_monitor.tap_req_id.eq(req_source.req_id),
            self.txn_monitor.tap_tag.eq(req_source.tag),
            self.txn_monitor.tap_bar_hit.eq(req_source.bar_hit),
        ]

        # Conditionally connect optional fields (attr, at, first_be, last_be)
        if hasattr(req_source, 'first_be'):
            self.comb += self.txn_monitor.tap_first_be.eq(req_source.first_be)
        if hasattr(req_source, 'last_be'):
            self.comb += self.txn_monitor.tap_last_be.eq(req_source.last_be)
        if hasattr(req_source, 'attr'):
            self.comb += self.txn_monitor.tap_attr.eq(req_source.attr)
        if hasattr(req_source, 'at'):
            self.comb += self.txn_monitor.tap_at.eq(req_source.at)

        # Connect monitor control/status to BSA registers
        self.comb += [
            self.txn_monitor.enable.eq(self.bsa_regs.txn_enable),
            self.txn_monitor.clear.eq(self.bsa_regs.txn_clear),
            self.bsa_regs.txn_fifo_data.eq(self.txn_monitor.fifo_data),
            self.bsa_regs.txn_fifo_valid.eq(~self.txn_monitor.fifo_empty),
            self.txn_monitor.fifo_read.eq(self.bsa_regs.txn_fifo_read),
        ]

        # MSI (placeholder - Phase 3 will add full MSI-X) -------------------------
        self.pcie_msi = LitePCIeMSI()
        self.comb += self.pcie_msi.source.connect(self.pcie_phy.msi)

        # Vivado ------------------------------------------------------------------
        platform.toolchain.pre_synthesis_commands.append("set_property XPM_LIBRARIES XPM_MEMORY [current_project]")


# =============================================================================
# Build
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="BSA PCIe Exerciser Phase 2")
    parser.add_argument("--build",        action="store_true", help="Build bitstream")
    parser.add_argument("--load",         action="store_true", help="Load bitstream via JTAG")
    parser.add_argument("--sys-clk-freq", default=125e6, type=float, help="System clock frequency")
    parser.add_argument("--output-dir",   default="build/bsa_exerciser", help="Build output directory")
    args = parser.parse_args()

    # Import platform here to avoid issues when file is viewed standalone
    from bsa_pcie_exerciser.platform.spec_a7_platform import Platform

    platform = Platform(variant="xc7a35t")

    soc = BSAExerciserSoC(platform,
        sys_clk_freq = int(args.sys_clk_freq),
    )

    builder = Builder(soc, output_dir=args.output_dir)

    if args.build:
        builder.build()

    if args.load:
        prog = platform.create_programmer()
        prog.load_bitstream(os.path.join(args.output_dir, "gateware", "top.bit"))


if __name__ == "__main__":
    main()