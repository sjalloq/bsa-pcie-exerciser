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
        super().__init__(platform, sys_clk_freq, crg_cls=SquirrelCRG, **kwargs)

        # USB clock domain (from FT601's 100MHz clock or simulation)
        self.cd_usb = ClockDomain()

        # Use injected USB PHY or create from platform pads
        if usb_phy is not None:
            # Simulation mode: use injected PHY stub
            self.usb_phy = usb_phy
            # In simulation, the USB clock domain is managed by the testbench
        else:
            # Hardware mode: create FT601Sync from platform pads
            usb_pads = platform.request("usb_fifo")

            # Connect FT601 clock to USB domain
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
        if usb_phy is None:
            platform.add_period_constraint(usb_pads.clk, 1e9/100e6)
            platform.add_false_path_constraints(self.crg.cd_sys.clk, self.cd_usb.clk)

    def _add_usb_monitor(self):
        """Add USB TLP monitor subsystem on channel 1."""
        # Free-running timestamp counter
        self.timestamp = Signal(64)
        self.sync += self.timestamp.eq(self.timestamp + 1)

        # USB Monitor Subsystem
        self.usb_monitor = USBMonitorSubsystem(
            data_width=self.pcie_phy.data_width,
            payload_fifo_depth=512,
        )

        # Connect timestamp
        self.comb += self.usb_monitor.timestamp.eq(self.timestamp)

        # -------------------------------------------------------------------------
        # RX Tap (depacketizer outputs - inbound TLPs)
        # -------------------------------------------------------------------------

        req_source = self.pcie_endpoint.depacketizer.req_source
        cpl_source = self.pcie_endpoint.depacketizer.cmp_source

        # RX Request tap
        self.comb += [
            self.usb_monitor.rx_req_valid.eq(req_source.valid),
            self.usb_monitor.rx_req_ready.eq(req_source.ready),
            self.usb_monitor.rx_req_first.eq(req_source.first),
            self.usb_monitor.rx_req_last.eq(req_source.last),
            self.usb_monitor.rx_req_we.eq(req_source.we),
            self.usb_monitor.rx_req_adr.eq(req_source.adr),
            self.usb_monitor.rx_req_len.eq(req_source.len),
            self.usb_monitor.rx_req_req_id.eq(req_source.req_id),
            self.usb_monitor.rx_req_tag.eq(req_source.tag),
            self.usb_monitor.rx_req_dat.eq(req_source.dat),
        ]

        # Optional RX request fields
        if hasattr(req_source, 'first_be'):
            self.comb += self.usb_monitor.rx_req_first_be.eq(req_source.first_be)
        if hasattr(req_source, 'last_be'):
            self.comb += self.usb_monitor.rx_req_last_be.eq(req_source.last_be)
        if hasattr(req_source, 'attr'):
            self.comb += self.usb_monitor.rx_req_attr.eq(req_source.attr)
        if hasattr(req_source, 'at'):
            self.comb += self.usb_monitor.rx_req_at.eq(req_source.at)
        if hasattr(req_source, 'bar_hit'):
            self.comb += self.usb_monitor.rx_req_bar_hit.eq(req_source.bar_hit)

        # RX Completion tap
        self.comb += [
            self.usb_monitor.rx_cpl_valid.eq(cpl_source.valid),
            self.usb_monitor.rx_cpl_ready.eq(cpl_source.ready),
            self.usb_monitor.rx_cpl_first.eq(cpl_source.first),
            self.usb_monitor.rx_cpl_last.eq(cpl_source.last),
            self.usb_monitor.rx_cpl_adr.eq(cpl_source.adr),
            self.usb_monitor.rx_cpl_len.eq(cpl_source.len),
            self.usb_monitor.rx_cpl_req_id.eq(cpl_source.req_id),
            self.usb_monitor.rx_cpl_tag.eq(cpl_source.tag),
            self.usb_monitor.rx_cpl_dat.eq(cpl_source.dat),
        ]

        # Optional RX completion fields
        if hasattr(cpl_source, 'status'):
            self.comb += self.usb_monitor.rx_cpl_status.eq(cpl_source.status)
        if hasattr(cpl_source, 'cmp_id'):
            self.comb += self.usb_monitor.rx_cpl_cmp_id.eq(cpl_source.cmp_id)
        if hasattr(cpl_source, 'byte_count'):
            self.comb += self.usb_monitor.rx_cpl_byte_count.eq(cpl_source.byte_count)

        # -------------------------------------------------------------------------
        # TX Tap (packetizer inputs - outbound TLPs)
        # -------------------------------------------------------------------------

        req_sink = self.pcie_endpoint.packetizer.req_sink
        cpl_sink = self.pcie_endpoint.packetizer.cmp_sink

        # TX Request tap
        self.comb += [
            self.usb_monitor.tx_req_valid.eq(req_sink.valid),
            self.usb_monitor.tx_req_ready.eq(req_sink.ready),
            self.usb_monitor.tx_req_first.eq(req_sink.first),
            self.usb_monitor.tx_req_last.eq(req_sink.last),
            self.usb_monitor.tx_req_we.eq(req_sink.we),
            self.usb_monitor.tx_req_adr.eq(req_sink.adr),
            self.usb_monitor.tx_req_len.eq(req_sink.len),
            self.usb_monitor.tx_req_req_id.eq(req_sink.req_id),
            self.usb_monitor.tx_req_tag.eq(req_sink.tag),
            self.usb_monitor.tx_req_dat.eq(req_sink.dat),
        ]

        # Optional TX request fields
        if hasattr(req_sink, 'first_be'):
            self.comb += self.usb_monitor.tx_req_first_be.eq(req_sink.first_be)
        if hasattr(req_sink, 'last_be'):
            self.comb += self.usb_monitor.tx_req_last_be.eq(req_sink.last_be)
        if hasattr(req_sink, 'attr'):
            self.comb += self.usb_monitor.tx_req_attr.eq(req_sink.attr)
        if hasattr(req_sink, 'at'):
            self.comb += self.usb_monitor.tx_req_at.eq(req_sink.at)
        if hasattr(req_sink, 'pasid_en'):
            self.comb += self.usb_monitor.tx_req_pasid_valid.eq(req_sink.pasid_en)
        if hasattr(req_sink, 'pasid_val'):
            self.comb += self.usb_monitor.tx_req_pasid.eq(req_sink.pasid_val)
        if hasattr(req_sink, 'privileged'):
            self.comb += self.usb_monitor.tx_req_privileged.eq(req_sink.privileged)
        if hasattr(req_sink, 'execute'):
            self.comb += self.usb_monitor.tx_req_execute.eq(req_sink.execute)

        # TX Completion tap
        self.comb += [
            self.usb_monitor.tx_cpl_valid.eq(cpl_sink.valid),
            self.usb_monitor.tx_cpl_ready.eq(cpl_sink.ready),
            self.usb_monitor.tx_cpl_first.eq(cpl_sink.first),
            self.usb_monitor.tx_cpl_last.eq(cpl_sink.last),
            self.usb_monitor.tx_cpl_adr.eq(cpl_sink.adr),
            self.usb_monitor.tx_cpl_len.eq(cpl_sink.len),
            self.usb_monitor.tx_cpl_req_id.eq(cpl_sink.req_id),
            self.usb_monitor.tx_cpl_tag.eq(cpl_sink.tag),
            self.usb_monitor.tx_cpl_dat.eq(cpl_sink.dat),
        ]

        # Optional TX completion fields
        if hasattr(cpl_sink, 'status'):
            self.comb += self.usb_monitor.tx_cpl_status.eq(cpl_sink.status)
        if hasattr(cpl_sink, 'cmp_id'):
            self.comb += self.usb_monitor.tx_cpl_cmp_id.eq(cpl_sink.cmp_id)
        if hasattr(cpl_sink, 'byte_count'):
            self.comb += self.usb_monitor.tx_cpl_byte_count.eq(cpl_sink.byte_count)

        # -------------------------------------------------------------------------
        # Control (from BSA registers)
        # -------------------------------------------------------------------------

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
