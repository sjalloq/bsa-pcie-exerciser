#!/usr/bin/env python3
#
# Etherbone Standalone Testbench
#
# Copyright (c) 2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Minimal test platform for testing USBEtherbone in isolation.
# Uses FT601Stub + USBCore + USBEtherbone + Wishbone SRAM.
#

import os
import sys

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream, wishbone

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tbench.common.ft601_stub import FT601StubWithFIFO

from bsa_pcie_exerciser.gateware.usb import USBCore, USBEtherbone


# =============================================================================
# Wishbone SRAM for Testing
# =============================================================================

class WishboneSRAM(LiteXModule):
    """
    Simple Wishbone SRAM for Etherbone testing.

    Provides a 4KB SRAM with an ID register at address 0x1000.
    """

    def __init__(self, size=4096):
        self.bus = wishbone.Interface()

        # # #

        # Memory array (word-addressed)
        mem = Memory(32, size // 4)
        self.specials += mem

        port = mem.get_port(write_capable=True)
        self.specials += port

        # ID register (read-only)
        self.id_value = Signal(32, reset=0xED0113B5)

        # Address decode: 0x1000+ = ID register, else SRAM
        is_id_access = Signal()
        self.comb += is_id_access.eq(self.bus.adr[10:] != 0)



        # FSM for Wishbone transactions
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.bus.cyc & self.bus.stb,
                If(is_id_access,
                    NextState("ID_ACCESS")
                ).Else(
                    NextState("SRAM_ACCESS")
                )
            )
        )

        # Drive address phase signals.
        self.comb += [
            port.adr.eq(self.bus.adr[:10]),
            port.dat_w.eq(self.bus.dat_w),
            port.we.eq(self.bus.we & self.bus.cyc & self.bus.stb & ~is_id_access & fsm.ongoing("IDLE"))
        ]

        fsm.act("ID_ACCESS",
            self.bus.dat_r.eq(self.id_value),
            self.bus.ack.eq(1),
            NextState("IDLE")
        )

        fsm.act("SRAM_ACCESS",
            self.bus.dat_r.eq(port.dat_r),
            self.bus.ack.eq(1),
            NextState("IDLE")
        )


# =============================================================================
# Etherbone Test Platform
# =============================================================================

class EtherboneTestbench(LiteXModule):
    """
    Minimal test platform for USBEtherbone testing.

    Contains:
    - FT601StubWithFIFO for USB PHY simulation
    - USBCore for packet framing/routing
    - USBEtherbone for Etherbone protocol
    - WishboneSRAM for read/write testing

    All signals exposed for cocotb access.
    """

    def __init__(self, sys_clk_freq=100e6):
        # Clock domain (driven by cocotb)
        self.cd_sys = ClockDomain("sys")

        # =====================================================================
        # USB PHY Stub
        # =====================================================================

        self.usb_phy = usb_phy = FT601StubWithFIFO(dw=32, rx_depth=64, tx_depth=64)

        # =====================================================================
        # USB Core
        # =====================================================================

        self.usb_core = usb_core = USBCore(usb_phy, sys_clk_freq)

        # =====================================================================
        # Etherbone (Module Under Test)
        # =====================================================================

        self.etherbone = etherbone = USBEtherbone(usb_core, channel_id=0, buffer_depth=16)

        # =====================================================================
        # Wishbone SRAM
        # =====================================================================

        self.sram = sram = WishboneSRAM(size=4096)

        # Connect Etherbone to SRAM
        self.comb += etherbone.master.bus.connect(sram.bus)

        # =====================================================================
        # Signal Exposure for Cocotb
        # =====================================================================

        # Host -> Device injection (cocotb drives these)
        self.usb_inject_valid = Signal(name="usb_inject_valid")
        self.usb_inject_ready = Signal(name="usb_inject_ready")
        self.usb_inject_data = Signal(32, name="usb_inject_data")

        # Device -> Host capture (cocotb reads these)
        self.usb_capture_valid = Signal(name="usb_capture_valid")
        self.usb_capture_ready = Signal(name="usb_capture_ready")
        self.usb_capture_data = Signal(32, name="usb_capture_data")

        # Backpressure control
        self.usb_tx_backpressure = Signal(name="usb_tx_backpressure")

        # FIFO status
        self.usb_rx_fifo_level = Signal(8, name="usb_rx_fifo_level")
        self.usb_tx_fifo_level = Signal(8, name="usb_tx_fifo_level")

        # Wire external signals to USB stub
        self.comb += [
            # Host -> Device
            usb_phy.inject_valid.eq(self.usb_inject_valid),
            self.usb_inject_ready.eq(usb_phy.inject_ready),
            usb_phy.inject_data.eq(self.usb_inject_data),

            # Device -> Host
            self.usb_capture_valid.eq(usb_phy.capture_valid),
            usb_phy.capture_ready.eq(self.usb_capture_ready),
            self.usb_capture_data.eq(usb_phy.capture_data),

            # Control
            usb_phy.tx_backpressure.eq(self.usb_tx_backpressure),

            # Status
            self.usb_rx_fifo_level.eq(usb_phy.rx_fifo_level),
            self.usb_tx_fifo_level.eq(usb_phy.tx_fifo_level),
        ]


# =============================================================================
# Verilog Generation
# =============================================================================

def generate_verilog():
    """Generate Verilog for cocotb simulation."""
    from migen.fhdl.verilog import convert

    testbench = EtherboneTestbench()

    ios = {
        # Clock domain
        testbench.cd_sys.clk,
        testbench.cd_sys.rst,

        # USB inject (Host -> Device)
        testbench.usb_inject_valid,
        testbench.usb_inject_ready,
        testbench.usb_inject_data,

        # USB capture (Device -> Host)
        testbench.usb_capture_valid,
        testbench.usb_capture_ready,
        testbench.usb_capture_data,

        # USB control/status
        testbench.usb_tx_backpressure,
        testbench.usb_rx_fifo_level,
        testbench.usb_tx_fifo_level,
    }

    output = convert(testbench, ios=ios, name="tb_etherbone")

    # Write Verilog to build directory
    build_dir = "build/sim"
    os.makedirs(build_dir, exist_ok=True)

    orig_dir = os.getcwd()
    os.chdir(build_dir)
    output.write("tb_etherbone.v")
    os.chdir(orig_dir)

    print(f"Generated {build_dir}/tb_etherbone.v")


if __name__ == "__main__":
    generate_verilog()
