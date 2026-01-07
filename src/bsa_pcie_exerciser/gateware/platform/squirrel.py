#!/usr/bin/env python3
#
# Squirrel / CaptainDMA Platform
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Pin-compatible boards using XC7A35T-FGG484.
# Constraints derived from pcileech-fpga XDC files.
#

from litex.build.generic_platform import *
from litex.build.xilinx import Xilinx7SeriesPlatform
from litex.build.openfpgaloader import OpenFPGALoader


# -----------------------------------------------------------------------------
# I/O Definitions
# -----------------------------------------------------------------------------

_io = [
    # System Clock - 100MHz oscillator
    ("clk100", 0, Pins("H4"), IOStandard("LVCMOS33")),

    # User LEDs
    ("user_led", 0, Pins("Y6"), IOStandard("LVCMOS33")),
    ("user_led", 1, Pins("AB5"), IOStandard("LVCMOS33")),

    # User Switches (directly active low)
    ("user_sw", 0, Pins("AB3"), IOStandard("LVCMOS33")),
    ("user_sw", 1, Pins("AA5"), IOStandard("LVCMOS33")),

    # FT2232 Reset (directly active low, directly active in FPGA to FT2232)
    ("ft2232_rst_n", 0, Pins("F21"), IOStandard("LVCMOS33")),

    # PCIe x1
    ("pcie_x1", 0,
        Subsignal("rst_n", Pins("B13"), IOStandard("LVCMOS33"), Misc("PULLUP=TRUE")),
        Subsignal("clk_p", Pins("F6")),
        Subsignal("clk_n", Pins("E6")),
        Subsignal("rx_p",  Pins("B10")),
        Subsignal("rx_n",  Pins("A10")),
        Subsignal("tx_p",  Pins("B6")),
        Subsignal("tx_n",  Pins("A6")),
    ),

    # PCIe auxiliary signals
    ("pcie_present", 0, Pins("A13"), IOStandard("LVCMOS33")),
    ("pcie_wake_n",  0, Pins("A14"), IOStandard("LVCMOS33")),

    # FT601 USB 3.0 FIFO
    ("usb_fifo", 0,
        # Clock from FT601 (100 MHz)
        Subsignal("clk", Pins("W19"), IOStandard("LVCMOS33")),

        # 32-bit bidirectional data bus
        Subsignal("data", Pins(
            "N13  N14  N15  P15  P16  N17  P17  R17 "   # D[7:0]
            "P19  R18  R19  T18  U18  V18  V19  V17 "   # D[15:8]
            "W20  Y19  T21  T20  U21  V20  W22  W21 "   # D[23:16]
            "Y22  Y21  AA21 AB22 AA20 AB21 AA19 AB20"), # D[31:24]
            IOStandard("LVCMOS33"), Misc("SLEW=FAST")),

        # Byte enables
        Subsignal("be", Pins("Y18 AA18 AB18 W17"),
            IOStandard("LVCMOS33"), Misc("SLEW=FAST")),

        # Control signals (directly active low)
        Subsignal("rxf_n",  Pins("AB8"),  IOStandard("LVCMOS33")),  # RX FIFO not empty
        Subsignal("txe_n",  Pins("AA8"),  IOStandard("LVCMOS33")),  # TX FIFO not full
        Subsignal("rd_n",   Pins("AA6"),  IOStandard("LVCMOS33"), Misc("SLEW=FAST")),  # Read strobe
        Subsignal("wr_n",   Pins("AB7"),  IOStandard("LVCMOS33"), Misc("SLEW=FAST")),  # Write strobe
        Subsignal("oe_n",   Pins("AB6"),  IOStandard("LVCMOS33"), Misc("SLEW=FAST")),  # Output enable
        Subsignal("siwu_n", Pins("Y8"),   IOStandard("LVCMOS33"), Misc("SLEW=FAST")),  # Send immediate / wake up
        Subsignal("rst_n",  Pins("Y9"),   IOStandard("LVCMOS33"), Misc("SLEW=FAST")),  # Reset
    ),
]


# -----------------------------------------------------------------------------
# Platform Class
# -----------------------------------------------------------------------------

class Platform(Xilinx7SeriesPlatform):
    def __init__(self, variant="xc7a35t", toolchain="vivado"):
        Xilinx7SeriesPlatform.__init__(self, f"{variant}fgg484-2", _io, toolchain=toolchain)

        self.toolchain.bitstream_commands = [
            "set_property CFGBVS Vcco [current_design]",
            "set_property CONFIG_VOLTAGE 3.3 [current_design]",
            "set_property BITSTREAM.CONFIG.SPI_BUSWIDTH 4 [current_design]",
            "set_property BITSTREAM.GENERAL.COMPRESS TRUE [current_design]",
            "set_property BITSTREAM.CONFIG.SPI_FALL_EDGE YES [current_design]",
            "set_property BITSTREAM.CONFIG.CONFIGRATE 66 [current_design]",
        ]
        self.toolchain.additional_commands += [
            "report_timing -delay_type max -max_paths 50 -nworst 10 -path_type full -sort_by slack "
            "-file {build_name}_timing_max.rpt",
            "report_timing -delay_type min -max_paths 50 -nworst 10 -path_type full -sort_by slack "
            "-file {build_name}_timing_min.rpt",
        ]

    def create_programmer(self, name="openocd"):
        return OpenFPGALoader(cable="ft2232")

    def do_finalize(self, fragment):
        Xilinx7SeriesPlatform.do_finalize(self, fragment)
        self.add_period_constraint(self.lookup_request("clk100", loose=True), 1e9/100e6)
        self.add_period_constraint(self.lookup_request("pcie_x1", loose=True).clk_p, 1e9/100e6)
