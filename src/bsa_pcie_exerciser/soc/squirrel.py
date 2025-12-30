#
# BSA PCIe Exerciser - Squirrel/CaptainDMA Platform Support
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

from migen import *
from litex.gen import *
from litex.soc.cores.clock import S7PLL


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
