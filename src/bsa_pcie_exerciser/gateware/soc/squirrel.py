#
# BSA PCIe Exerciser - Squirrel/CaptainDMA Platform Support
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

from migen import *
from migen.genlib.resetsync import AsyncResetSynchronizer

from litex.gen import *
from litex.soc.cores.clock import S7PLL

from bsa_pcie_exerciser.gateware.soc.base import BSAExerciserSoC
from bsa_pcie_exerciser.gateware.usb import FT601Sync, USBCore, Etherbone, USBMonitorSubsystem


class SquirrelCRG(LiteXModule):
    """
    Clock Reset Generator for Squirrel/CaptainDMA.

    Uses 100MHz oscillator on board, generates system clock.
    PCIe clock comes from PHY (100MHz refclk from PCIe connector).
    """

    def __init__(self, platform, sys_clk_freq):
        self.rst    = Signal()
        self.cd_sys = ClockDomain()

        # 100MHz oscillator on Squirrel/CaptainDMA
        clk100 = platform.request("clk100")

        # PLL: 100MHz -> sys_clk_freq
        self.pll = pll = S7PLL(speedgrade=-2)
        pll.register_clkin(clk100, 100e6)
        pll.create_clkout(self.cd_sys, sys_clk_freq, margin=0)


class SquirrelSoC(BSAExerciserSoC):
    """
    Squirrel SoC with USB Etherbone and transaction monitoring.

    Extends the base BSA Exerciser SoC with:
    - FT601 USB 3.0 PHY
    - USB channel multiplexer
    - Etherbone for CSR access via USB (channel 0)
    - Transaction monitor streaming via USB (channel 1)

    Parameters
    ----------
    usb_phy : optional
        Pre-instantiated USB PHY (for simulation). If None, creates FT601Sync
        from platform USB FIFO pads.
    """

    def __init__(self, platform, sys_clk_freq=125e6, usb_phy=None, **kwargs):
        # Initialize base SoC with SquirrelCRG
        super().__init__(
            platform,
            sys_clk_freq,
            crg_cls=SquirrelCRG,
            pcie_gt_locn="X0Y2",
            **kwargs,
        )

        simulation = kwargs.get('simulation', False)

        # USB clock domain (from FT601's 100MHz clock or simulation)
        self.cd_usb = ClockDomain()

        # Use injected USB PHY or create from platform pads
        if simulation:
            # Simulation mode: use injected PHY stub
            self.usb_phy = usb_phy
        else:
            # Hardware mode: create FT601Sync from platform pads
            usb_pads = platform.request("usb_fifo")
            self.comb += self.cd_usb.clk.eq(usb_pads.clk)
            self.specials += AsyncResetSynchronizer(self.cd_usb, self.crg.rst)

            # FT601 PHY
            self.usb_phy = FT601Sync(usb_pads, dw=32, timeout=1024)

        # USB Core (packetizer + crossbar)
        self.usb_core = USBCore(self.usb_phy, sys_clk_freq)

        # Etherbone (USB -> Wishbone) on channel 0
        # buffer_depth must accommodate the largest burst: 1 base_addr + N read/write addresses
        # Default of 4 causes deadlock for bursts > 3 addresses (PacketFIFO needs entire
        # payload before 'last' commits the params, but full FIFO blocks upstream)
        self.etherbone = Etherbone(self.usb_core, channel_id=0, buffer_depth=16)
        self.bus.add_master("usb", master=self.etherbone.master.bus)

        # USB Monitor Subsystem on channel 1
        self._add_usb_monitor()

        # Clock constraints (only for hardware mode with real USB pads)
        if not simulation:
            platform.add_period_constraint(usb_pads.clk, 1e9/100e6)

        # -----------------------------------------------------------------
        # Board I/O: LEDs and FT2232 Reset
        # -----------------------------------------------------------------

        # LED1: PCIe link up status
        led1 = platform.request("user_led", 0)
        self.comb += led1.eq(self.pcie_phy._link_status.fields.status)

        # LED2: Heartbeat (~1Hz blink at 125MHz sys_clk)
        # Use bit 26 of timestamp counter: 125MHz / 2^26 ≈ 1.86Hz
        led2 = platform.request("user_led", 1)
        self.comb += led2.eq(self.timestamp[26])

        # FT2232 Reset: Drive high (inactive) to keep JTAG chip operational
        ft2232_rst_n = platform.request("ft2232_rst_n")
        self.comb += ft2232_rst_n.eq(1)

        # -----------------------------------------------------------------
        # Async clock paths
        # -----------------------------------------------------------------

        if not simulation:
            platform.add_false_path_constraint(self.crg.cd_sys.clk, self.cd_usb.clk)
            platform.add_false_path_constraint(self.cd_usb.clk, self.crg.cd_sys.clk)

            platform.toolchain.pre_placement_commands.append(
                "set_clock_groups -asynchronous "
                "-group [get_clocks squirrelsoc_clkout] "
                "-group [get_clocks {{clk125_clk clk250_clk squirrelsoc_s7pciephy_clkout*}}]"
            )

        # ---------------------------------------------------------------------
        # FT601 Timing Constraints
        # ---------------------------------------------------------------------

        if not simulation:

            # Input delays for FT601 signals (data, status)
            platform.add_platform_command(
                "set_input_delay -clock [get_clocks usb_fifo_clk] -min 6.5 "
                "[get_ports {{usb_fifo_data[*]}}]"
            )
            platform.add_platform_command(
                "set_input_delay -clock [get_clocks usb_fifo_clk] -max 7.0 "
                "[get_ports {{usb_fifo_data[*]}}]"
            )
            platform.add_platform_command(
                "set_input_delay -clock [get_clocks usb_fifo_clk] -min 6.5 "
                "[get_ports {{usb_fifo_rxf_n usb_fifo_txe_n}}]"
            )
            platform.add_platform_command(
                "set_input_delay -clock [get_clocks usb_fifo_clk] -max 7.0 "
                "[get_ports {{usb_fifo_rxf_n usb_fifo_txe_n}}]"
            )

            # Output delays for FT601 control signals
            platform.add_platform_command(
                "set_output_delay -clock [get_clocks usb_fifo_clk] -min 4.8 "
                "[get_ports {{usb_fifo_wr_n usb_fifo_rd_n usb_fifo_oe_n}}]"
            )
            platform.add_platform_command(
                "set_output_delay -clock [get_clocks usb_fifo_clk] -max 1.0 "
                "[get_ports {{usb_fifo_wr_n usb_fifo_rd_n usb_fifo_oe_n}}]"
            )

            # Output delays for FT601 data and byte enables
            platform.add_platform_command(
                "set_output_delay -clock [get_clocks usb_fifo_clk] -min 4.8 "
                "[get_ports {{usb_fifo_be[*] usb_fifo_data[*]}}]"
            )
            platform.add_platform_command(
                "set_output_delay -clock [get_clocks usb_fifo_clk] -max 1.0 "
                "[get_ports {{usb_fifo_be[*] usb_fifo_data[*]}}]"
            )


    def _add_usb_monitor(self):
        """Add USB TLP monitor subsystem on channel 1."""
        # Free-running timestamp counter
        self.timestamp = Signal(64)
        self.sync += self.timestamp.eq(self.timestamp + 1)

        # Get stream endpoints to tap
        rx_req_source = self.pcie_endpoint.depacketizer.req_source
        rx_cpl_source = self.pcie_endpoint.depacketizer.cmp_source
        tx_req_sink = self.pcie_endpoint.packetizer.req_sink
        tx_cpl_sink = self.pcie_endpoint.packetizer.cmp_sink

        # USB Monitor Subsystem - taps the stream endpoints directly
        # Pipeline registers inside USBMonitorSubsystem break timing paths
        self.usb_monitor = USBMonitorSubsystem(
            rx_req_source=rx_req_source,
            rx_cpl_source=rx_cpl_source,
            tx_req_sink=tx_req_sink,
            tx_cpl_sink=tx_cpl_sink,
            data_width=self.pcie_phy.data_width,
            payload_fifo_depth=512,
        )

        # Connect timestamp
        self.comb += self.usb_monitor.timestamp.eq(self.timestamp)

        # Connect control signals from USB_MON registers
        self.comb += [
            self.usb_monitor.rx_enable.eq(self.bsa_regs.usb_mon_rx_enable),
            self.usb_monitor.tx_enable.eq(self.bsa_regs.usb_mon_tx_enable),
            self.usb_monitor.clear_stats.eq(self.bsa_regs.usb_mon_clear_stats),
        ]

        # Connect statistics to USB_MON registers
        self.comb += [
            self.bsa_regs.usb_mon_rx_captured.eq(self.usb_monitor.rx_captured),
            self.bsa_regs.usb_mon_rx_dropped.eq(self.usb_monitor.rx_dropped),
            self.bsa_regs.usb_mon_tx_captured.eq(self.usb_monitor.tx_captured),
            self.bsa_regs.usb_mon_tx_dropped.eq(self.usb_monitor.tx_dropped),
            self.bsa_regs.usb_mon_rx_truncated.eq(self.usb_monitor.rx_truncated),
            self.bsa_regs.usb_mon_tx_truncated.eq(self.usb_monitor.tx_truncated),
        ]

        # Connect to USB channel 1
        monitor_port = self.usb_core.crossbar.get_port(1)
        self.comb += self.usb_monitor.source.connect(monitor_port.sink)
