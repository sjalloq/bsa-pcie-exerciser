#
# BSA PCIe Exerciser - SPEC-A7 Platform Support
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

from migen import *
from litex.gen import *
from litex.soc.cores.clock import S7PLL


class SPECA7CRG(LiteXModule):
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
