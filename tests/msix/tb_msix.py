#
# MSI-X Testbench Wrapper
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Migen testbench wrapper for MSI-X subsystem testing with Cocotb.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import request_layout, completion_layout


class MockPHY:
    """Mock PHY providing minimal interface for MSI-X testing."""
    def __init__(self, data_width=64, device_id=0x0001):
        self.data_width = data_width
        self.id = Signal(16, reset=device_id)
        self.bar0_mask = 0xFFF  # 4KB


class MockMasterPort(LiteXModule):
    """
    Mock master port for capturing MSI-X Memory Write TLPs.

    Provides the same interface as crossbar.get_master_port() but
    exposes signals directly for testbench access.
    """
    def __init__(self, data_width):
        self.data_width = data_width
        self.channel = Signal(8)

        # Source: DUT sends requests here (MSI-X Memory Writes)
        self.source = stream.Endpoint(request_layout(data_width))

        # Sink: Completions back to DUT (unused for MSI-X, just tie off)
        self.sink = stream.Endpoint(completion_layout(data_width))


class MSIXTestbench(LiteXModule):
    """
    Testbench wrapper for MSI-X subsystem.

    Instantiates the MSI-X table, PBA, and controller with
    test-friendly interfaces exposed at the top level.

    Interfaces:
    - bar2_*: Table request/completion (host access)
    - bar5_*: PBA request/completion (host access)
    - msi_*: MSI-X TLP output (Memory Writes to host)
    - sw_*: Software trigger interface
    - irqs: Hardware IRQ inputs
    """

    def __init__(self, data_width=64, n_vectors=2048, n_irqs=32):
        self.data_width = data_width
        self.n_vectors = n_vectors
        self.n_irqs = n_irqs

        # Clock domain
        self.cd_sys = ClockDomain("sys")

        # Create mock PHY
        self.phy = MockPHY(data_width)

        # =====================================================================
        # Top-level signals with stable names for testbench
        # =====================================================================

        # BAR2 (Table) request interface
        self.bar2_req_sink_valid    = Signal(name="bar2_req_sink_valid")
        self.bar2_req_sink_ready    = Signal(name="bar2_req_sink_ready")
        self.bar2_req_sink_first    = Signal(name="bar2_req_sink_first")
        self.bar2_req_sink_last     = Signal(name="bar2_req_sink_last")
        self.bar2_req_sink_we       = Signal(name="bar2_req_sink_we")
        self.bar2_req_sink_adr      = Signal(32, name="bar2_req_sink_adr")
        self.bar2_req_sink_len      = Signal(10, name="bar2_req_sink_len")
        self.bar2_req_sink_req_id   = Signal(16, name="bar2_req_sink_req_id")
        self.bar2_req_sink_tag      = Signal(8, name="bar2_req_sink_tag")
        self.bar2_req_sink_dat      = Signal(data_width, name="bar2_req_sink_dat")
        self.bar2_req_sink_first_be = Signal(4, name="bar2_req_sink_first_be")
        self.bar2_req_sink_last_be  = Signal(4, name="bar2_req_sink_last_be")

        # BAR2 (Table) completion interface
        self.bar2_cpl_source_valid  = Signal(name="bar2_cpl_source_valid")
        self.bar2_cpl_source_ready  = Signal(name="bar2_cpl_source_ready")
        self.bar2_cpl_source_first  = Signal(name="bar2_cpl_source_first")
        self.bar2_cpl_source_last   = Signal(name="bar2_cpl_source_last")
        self.bar2_cpl_source_dat    = Signal(data_width, name="bar2_cpl_source_dat")
        self.bar2_cpl_source_tag    = Signal(8, name="bar2_cpl_source_tag")
        self.bar2_cpl_source_err    = Signal(name="bar2_cpl_source_err")

        # BAR5 (PBA) request interface
        self.bar5_req_sink_valid    = Signal(name="bar5_req_sink_valid")
        self.bar5_req_sink_ready    = Signal(name="bar5_req_sink_ready")
        self.bar5_req_sink_first    = Signal(name="bar5_req_sink_first")
        self.bar5_req_sink_last     = Signal(name="bar5_req_sink_last")
        self.bar5_req_sink_we       = Signal(name="bar5_req_sink_we")
        self.bar5_req_sink_adr      = Signal(32, name="bar5_req_sink_adr")
        self.bar5_req_sink_len      = Signal(10, name="bar5_req_sink_len")
        self.bar5_req_sink_req_id   = Signal(16, name="bar5_req_sink_req_id")
        self.bar5_req_sink_tag      = Signal(8, name="bar5_req_sink_tag")
        self.bar5_req_sink_dat      = Signal(data_width, name="bar5_req_sink_dat")
        self.bar5_req_sink_first_be = Signal(4, name="bar5_req_sink_first_be")
        self.bar5_req_sink_last_be  = Signal(4, name="bar5_req_sink_last_be")

        # BAR5 (PBA) completion interface
        self.bar5_cpl_source_valid  = Signal(name="bar5_cpl_source_valid")
        self.bar5_cpl_source_ready  = Signal(name="bar5_cpl_source_ready")
        self.bar5_cpl_source_first  = Signal(name="bar5_cpl_source_first")
        self.bar5_cpl_source_last   = Signal(name="bar5_cpl_source_last")
        self.bar5_cpl_source_dat    = Signal(data_width, name="bar5_cpl_source_dat")
        self.bar5_cpl_source_tag    = Signal(8, name="bar5_cpl_source_tag")
        self.bar5_cpl_source_err    = Signal(name="bar5_cpl_source_err")

        # MSI-X TLP output interface
        self.msi_source_valid = Signal(name="msi_source_valid")
        self.msi_source_ready = Signal(name="msi_source_ready")
        self.msi_source_we    = Signal(name="msi_source_we")
        self.msi_source_adr   = Signal(64, name="msi_source_adr")
        self.msi_source_dat   = Signal(data_width, name="msi_source_dat")

        # =====================================================================
        # Import MSI-X modules here to avoid circular imports
        # =====================================================================

        from bsa_pcie_exerciser.msix.table import LitePCIeMSIXTable, LitePCIeMSIXPBA

        # =====================================================================
        # MSI-X Table (BAR2)
        # =====================================================================

        self.table = LitePCIeMSIXTable(
            phy        = self.phy,
            data_width = data_width,
            n_vectors  = n_vectors,
        )

        # Wire top-level signals to table interfaces
        self.comb += [
            self.table.req_sink.valid.eq(self.bar2_req_sink_valid),
            self.bar2_req_sink_ready.eq(self.table.req_sink.ready),
            self.table.req_sink.first.eq(self.bar2_req_sink_first),
            self.table.req_sink.last.eq(self.bar2_req_sink_last),
            self.table.req_sink.we.eq(self.bar2_req_sink_we),
            self.table.req_sink.adr.eq(self.bar2_req_sink_adr),
            self.table.req_sink.len.eq(self.bar2_req_sink_len),
            self.table.req_sink.req_id.eq(self.bar2_req_sink_req_id),
            self.table.req_sink.tag.eq(self.bar2_req_sink_tag),
            self.table.req_sink.dat.eq(self.bar2_req_sink_dat),
            self.table.req_sink.first_be.eq(self.bar2_req_sink_first_be),
            self.table.req_sink.last_be.eq(self.bar2_req_sink_last_be),

            self.bar2_cpl_source_valid.eq(self.table.cpl_source.valid),
            self.table.cpl_source.ready.eq(self.bar2_cpl_source_ready),
            self.bar2_cpl_source_first.eq(self.table.cpl_source.first),
            self.bar2_cpl_source_last.eq(self.table.cpl_source.last),
            self.bar2_cpl_source_dat.eq(self.table.cpl_source.dat),
            self.bar2_cpl_source_tag.eq(self.table.cpl_source.tag),
            self.bar2_cpl_source_err.eq(self.table.cpl_source.err),
        ]

        # =====================================================================
        # MSI-X PBA (BAR5)
        # =====================================================================

        self.pba = LitePCIeMSIXPBA(
            phy        = self.phy,
            data_width = data_width,
            n_vectors  = n_vectors,
        )

        # Wire top-level signals to PBA interfaces
        self.comb += [
            self.pba.req_sink.valid.eq(self.bar5_req_sink_valid),
            self.bar5_req_sink_ready.eq(self.pba.req_sink.ready),
            self.pba.req_sink.first.eq(self.bar5_req_sink_first),
            self.pba.req_sink.last.eq(self.bar5_req_sink_last),
            self.pba.req_sink.we.eq(self.bar5_req_sink_we),
            self.pba.req_sink.adr.eq(self.bar5_req_sink_adr),
            self.pba.req_sink.len.eq(self.bar5_req_sink_len),
            self.pba.req_sink.req_id.eq(self.bar5_req_sink_req_id),
            self.pba.req_sink.tag.eq(self.bar5_req_sink_tag),
            self.pba.req_sink.dat.eq(self.bar5_req_sink_dat),
            self.pba.req_sink.first_be.eq(self.bar5_req_sink_first_be),
            self.pba.req_sink.last_be.eq(self.bar5_req_sink_last_be),

            self.bar5_cpl_source_valid.eq(self.pba.cpl_source.valid),
            self.pba.cpl_source.ready.eq(self.bar5_cpl_source_ready),
            self.bar5_cpl_source_first.eq(self.pba.cpl_source.first),
            self.bar5_cpl_source_last.eq(self.pba.cpl_source.last),
            self.bar5_cpl_source_dat.eq(self.pba.cpl_source.dat),
            self.bar5_cpl_source_tag.eq(self.pba.cpl_source.tag),
            self.bar5_cpl_source_err.eq(self.pba.cpl_source.err),
        ]

        # =====================================================================
        # Mock Master Port (for MSI-X TLP output)
        # =====================================================================

        self.master_port = MockMasterPort(data_width)

        # Wire top-level signals to master port
        self.comb += [
            self.msi_source_valid.eq(self.master_port.source.valid),
            self.master_port.source.ready.eq(self.msi_source_ready),
            self.msi_source_we.eq(self.master_port.source.we),
            self.msi_source_adr.eq(self.master_port.source.adr),
            self.msi_source_dat.eq(self.master_port.source.dat),
        ]

        # =====================================================================
        # MSI-X Controller
        # =====================================================================

        # We need to create the controller manually since it normally
        # gets a master port from an endpoint's crossbar.

        self.fsm = fsm = FSM(reset_state="IDLE")

        # Software trigger interface
        self.sw_vector = Signal(11)
        self.sw_valid  = Signal()

        # Hardware IRQs
        self.irqs = Signal(n_irqs)

        # Pending hardware IRQs (sticky until serviced)
        hw_pending = Signal(n_irqs)
        hw_clear   = Signal(n_irqs)

        self.sync += hw_pending.eq((hw_pending | self.irqs) & ~hw_clear)

        # Priority encoder for hardware IRQs
        hw_irq_valid  = Signal()
        hw_irq_vector = Signal(11)

        for i in reversed(range(n_irqs)):
            self.comb += If(hw_pending[i],
                hw_irq_valid.eq(1),
                hw_irq_vector.eq(i),
            )

        # Vector selection - software has priority
        trigger_valid  = Signal()
        trigger_vector = Signal(11)
        trigger_is_sw  = Signal()

        self.comb += [
            If(self.sw_valid,
                trigger_valid.eq(1),
                trigger_vector.eq(self.sw_vector),
                trigger_is_sw.eq(1),
            ).Elif(hw_irq_valid,
                trigger_valid.eq(1),
                trigger_vector.eq(hw_irq_vector),
                trigger_is_sw.eq(0),
            ),
        ]

        # Latched vector info
        current_vector = Signal(11)
        current_is_sw  = Signal()

        # Table read interface
        table = self.table
        pba = self.pba
        port = self.master_port

        fsm.act("IDLE",
            If(trigger_valid,
                NextValue(current_vector, trigger_vector),
                NextValue(current_is_sw, trigger_is_sw),
                table.vector_num.eq(trigger_vector),
                table.read_en.eq(1),
                NextState("READ_TABLE"),
            ),
        )

        fsm.act("READ_TABLE",
            table.vector_num.eq(current_vector),
            If(table.read_valid,
                If(table.masked,
                    # Masked - set pending bit
                    pba.vector_num.eq(current_vector),
                    pba.set_pending.eq(1),
                    NextState("IDLE"),
                ).Else(
                    NextState("ISSUE_WRITE"),
                ),
            ),
        )

        # MSI-X Memory Write TLP generation
        self.comb += [
            port.source.channel.eq(port.channel),
            port.source.first.eq(1),
            port.source.last.eq(1),
            port.source.we.eq(1),
            port.source.adr.eq(table.msg_addr),
            port.source.req_id.eq(self.phy.id),
            port.source.tag.eq(0),
            port.source.len.eq(1),
            port.source.dat.eq(table.msg_data),
        ]

        fsm.act("ISSUE_WRITE",
            port.source.valid.eq(1),
            If(port.source.ready,
                pba.vector_num.eq(current_vector),
                pba.clear_pending.eq(1),
                If(~current_is_sw & (current_vector < n_irqs),
                    hw_clear.eq(1 << current_vector[:5]),  # Limit shift width
                ),
                NextState("IDLE"),
            ),
        )

        # Tie off unused completion sink
        self.comb += port.sink.ready.eq(1)

        # =====================================================================
        # Expose MSI-X TLP interface
        # =====================================================================

        self.msi_source = port.source

        # =====================================================================
        # Controller busy status
        # =====================================================================

        self.busy = Signal()
        self.comb += self.busy.eq(~fsm.ongoing("IDLE"))


def generate_verilog():
    """Generate Verilog for Cocotb simulation."""
    import os
    from migen.fhdl.verilog import convert

    # Create testbench with reduced vector count for faster simulation
    tb = MSIXTestbench(data_width=64, n_vectors=16, n_irqs=4)

    # Convert to Verilog
    # We need to specify the ios (inputs/outputs) for the top-level module
    ios = set()

    # Clock and reset
    ios.add(tb.cd_sys.clk)
    ios.add(tb.cd_sys.rst)

    # BAR2 (Table) request interface
    ios.add(tb.bar2_req_sink_valid)
    ios.add(tb.bar2_req_sink_ready)
    ios.add(tb.bar2_req_sink_first)
    ios.add(tb.bar2_req_sink_last)
    ios.add(tb.bar2_req_sink_we)
    ios.add(tb.bar2_req_sink_adr)
    ios.add(tb.bar2_req_sink_len)
    ios.add(tb.bar2_req_sink_req_id)
    ios.add(tb.bar2_req_sink_tag)
    ios.add(tb.bar2_req_sink_dat)
    ios.add(tb.bar2_req_sink_first_be)
    ios.add(tb.bar2_req_sink_last_be)

    # BAR2 (Table) completion interface
    ios.add(tb.bar2_cpl_source_valid)
    ios.add(tb.bar2_cpl_source_ready)
    ios.add(tb.bar2_cpl_source_first)
    ios.add(tb.bar2_cpl_source_last)
    ios.add(tb.bar2_cpl_source_dat)
    ios.add(tb.bar2_cpl_source_tag)
    ios.add(tb.bar2_cpl_source_err)

    # BAR5 (PBA) request interface
    ios.add(tb.bar5_req_sink_valid)
    ios.add(tb.bar5_req_sink_ready)
    ios.add(tb.bar5_req_sink_first)
    ios.add(tb.bar5_req_sink_last)
    ios.add(tb.bar5_req_sink_we)
    ios.add(tb.bar5_req_sink_adr)
    ios.add(tb.bar5_req_sink_len)
    ios.add(tb.bar5_req_sink_req_id)
    ios.add(tb.bar5_req_sink_tag)
    ios.add(tb.bar5_req_sink_dat)
    ios.add(tb.bar5_req_sink_first_be)
    ios.add(tb.bar5_req_sink_last_be)

    # BAR5 (PBA) completion interface
    ios.add(tb.bar5_cpl_source_valid)
    ios.add(tb.bar5_cpl_source_ready)
    ios.add(tb.bar5_cpl_source_first)
    ios.add(tb.bar5_cpl_source_last)
    ios.add(tb.bar5_cpl_source_dat)
    ios.add(tb.bar5_cpl_source_tag)
    ios.add(tb.bar5_cpl_source_err)

    # MSI-X TLP output interface
    ios.add(tb.msi_source_valid)
    ios.add(tb.msi_source_ready)
    ios.add(tb.msi_source_adr)
    ios.add(tb.msi_source_dat)
    ios.add(tb.msi_source_we)

    # Software trigger interface
    ios.add(tb.sw_vector)
    ios.add(tb.sw_valid)
    ios.add(tb.busy)

    # Hardware IRQs
    ios.add(tb.irqs)

    # Generate Verilog
    output = convert(tb, ios=ios, name="tb_msix")

    # Write Verilog and any memory init files
    build_dir = "build/sim"
    os.makedirs(build_dir, exist_ok=True)

    # Change to build dir so data files are written there
    orig_dir = os.getcwd()
    os.chdir(build_dir)
    output.write("tb_msix.v")
    os.chdir(orig_dir)

    print(f"Generated {build_dir}/tb_msix.v")


if __name__ == "__main__":
    generate_verilog()
