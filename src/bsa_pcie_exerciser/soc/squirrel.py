#
# BSA PCIe Exerciser - Squirrel/CaptainDMA Platform Support
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *
from litex.soc.cores.clock import S7PLL

from bsa_pcie_exerciser.soc.base import BSAExerciserSoC
from bsa_pcie_exerciser.usb import FT601Sync, USBCore, Etherbone


class SquirrelCRG(LiteXModule):
    """
    Clock Reset Generator for Squirrel/CaptainDMA.

    Uses 100MHz oscillator on board, generates system clock.
    PCIe clock comes from PHY (100MHz refclk from PCIe connector).
    """

    def __init__(self, platform, sys_clk_freq):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()

        # 100MHz oscillator on Squirrel
        clk100 = platform.request("clk100")

        # PLL: 100MHz -> sys_clk_freq
        self.pll = pll = S7PLL(speedgrade=-2)
        self.comb += pll.reset.eq(self.rst)
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq, margin=0)

        platform.add_false_path_constraints(self.cd_sys.clk, pll.clkin)


class SquirrelSoC(BSAExerciserSoC):
    """
    Squirrel SoC with USB Etherbone support.

    Extends the base BSA Exerciser SoC with:
    - FT601 USB 3.0 PHY
    - USB channel multiplexer
    - Etherbone for CSR access via USB
    """

    def __init__(self, platform, sys_clk_freq=125e6, **kwargs):
        # Initialize base SoC with SquirrelCRG
        super().__init__(platform, sys_clk_freq, crg_cls=SquirrelCRG, **kwargs)

        # USB clock domain (from FT601's 100MHz clock)
        self.cd_usb = ClockDomain()

        # Request USB FIFO pads
        usb_pads = platform.request("usb_fifo")

        # Connect FT601 clock to USB domain
        self.comb += self.cd_usb.clk.eq(usb_pads.clk)
        self.specials += AsyncResetSynchronizer(self.cd_usb, self.crg.rst)

        # FT601 PHY
        self.usb_phy = FT601Sync(usb_pads, dw=32, timeout=1024)

        # USB Core (packetizer + crossbar)
        self.usb_core = USBCore(self.usb_phy, sys_clk_freq)

        # Etherbone (USB -> Wishbone)
        self.etherbone = Etherbone(self.usb_core, channel_id=0)
        self.bus.add_master("usb", master=self.etherbone.master.bus)

        # Clock domain crossing constraints
        platform.add_false_path_constraints(self.crg.cd_sys.clk, self.cd_usb.clk)
