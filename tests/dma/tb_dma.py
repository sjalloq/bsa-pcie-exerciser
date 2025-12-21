#
# DMA Engine Testbench Wrapper
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Migen testbench wrapper for DMA engine testing with Cocotb.
# Tests both the DMA engine and BAR1 buffer handler.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import request_layout, completion_layout


class MockPHY:
    """Mock PHY providing minimal interface for DMA testing."""
    def __init__(self, data_width=64, device_id=0x0001):
        self.data_width = data_width
        self.id = Signal(16, reset=device_id)
        self.bar0_mask = 0xFFF  # 4KB
        self.bar1_mask = 0x3FFF  # 16KB


class DMATestbench(LiteXModule):
    """
    Testbench wrapper for DMA engine and buffer handler.

    Exposes signals for cocotb testing:
    - bar1_*: BAR1 (buffer) request/completion interfaces
    - dma_*: DMA control and status signals
    - tlp_req_*: DMA engine TLP request output
    - tlp_cpl_*: DMA engine TLP completion input
    """

    def __init__(self, data_width=64, buffer_size=1024):
        self.data_width = data_width
        self.buffer_size = buffer_size

        # Clock domain
        self.cd_sys = ClockDomain("sys")

        # Create mock PHY
        self.phy = MockPHY(data_width)

        # =====================================================================
        # Import DMA modules
        # =====================================================================

        from bsa_pcie_exerciser.dma import BSADMABuffer, BSADMABufferHandler, BSADMAEngine

        # =====================================================================
        # DMA Buffer (shared between handler and engine)
        # =====================================================================

        self.buffer = BSADMABuffer(size=buffer_size, data_width=data_width, simulation=True)

        # =====================================================================
        # BAR1 Handler (host access to buffer)
        # =====================================================================

        self.handler = BSADMABufferHandler(
            phy=self.phy,
            buffer=self.buffer,
            data_width=data_width,
        )

        # =====================================================================
        # DMA Engine
        # =====================================================================

        self.engine = BSADMAEngine(
            phy=self.phy,
            buffer=self.buffer,
            data_width=data_width,
            max_request_size=64,  # Smaller for faster testing
        )

        # =====================================================================
        # Top-level signals with stable names for testbench
        # =====================================================================

        # ----- BAR1 (Buffer) Request Interface -----
        self.bar1_req_sink_valid    = Signal(name="bar1_req_sink_valid")
        self.bar1_req_sink_ready    = Signal(name="bar1_req_sink_ready")
        self.bar1_req_sink_first    = Signal(name="bar1_req_sink_first")
        self.bar1_req_sink_last     = Signal(name="bar1_req_sink_last")
        self.bar1_req_sink_we       = Signal(name="bar1_req_sink_we")
        self.bar1_req_sink_adr      = Signal(32, name="bar1_req_sink_adr")
        self.bar1_req_sink_len      = Signal(10, name="bar1_req_sink_len")
        self.bar1_req_sink_req_id   = Signal(16, name="bar1_req_sink_req_id")
        self.bar1_req_sink_tag      = Signal(8, name="bar1_req_sink_tag")
        self.bar1_req_sink_dat      = Signal(data_width, name="bar1_req_sink_dat")
        self.bar1_req_sink_first_be = Signal(4, name="bar1_req_sink_first_be")
        self.bar1_req_sink_last_be  = Signal(4, name="bar1_req_sink_last_be")

        # ----- BAR1 (Buffer) Completion Interface -----
        self.bar1_cpl_source_valid  = Signal(name="bar1_cpl_source_valid")
        self.bar1_cpl_source_ready  = Signal(name="bar1_cpl_source_ready")
        self.bar1_cpl_source_first  = Signal(name="bar1_cpl_source_first")
        self.bar1_cpl_source_last   = Signal(name="bar1_cpl_source_last")
        self.bar1_cpl_source_dat    = Signal(data_width, name="bar1_cpl_source_dat")
        self.bar1_cpl_source_tag    = Signal(8, name="bar1_cpl_source_tag")
        self.bar1_cpl_source_err    = Signal(name="bar1_cpl_source_err")

        # ----- DMA Control Interface -----
        self.dma_trigger     = Signal(name="dma_trigger")
        self.dma_direction   = Signal(name="dma_direction")
        self.dma_no_snoop    = Signal(name="dma_no_snoop")
        self.dma_addr_type   = Signal(2, name="dma_addr_type")
        self.dma_bus_addr    = Signal(64, name="dma_bus_addr")
        self.dma_length      = Signal(32, name="dma_length")
        self.dma_offset      = Signal(32, name="dma_offset")

        # ----- DMA Status Interface -----
        self.dma_busy        = Signal(name="dma_busy")
        self.dma_status      = Signal(2, name="dma_status")
        self.dma_status_we   = Signal(name="dma_status_we")

        # ----- DMA TLP Request Output (Memory Read/Write to host) -----
        self.tlp_req_source_valid    = Signal(name="tlp_req_source_valid")
        self.tlp_req_source_ready    = Signal(name="tlp_req_source_ready")
        self.tlp_req_source_first    = Signal(name="tlp_req_source_first")
        self.tlp_req_source_last     = Signal(name="tlp_req_source_last")
        self.tlp_req_source_we       = Signal(name="tlp_req_source_we")
        self.tlp_req_source_adr      = Signal(64, name="tlp_req_source_adr")
        self.tlp_req_source_len      = Signal(10, name="tlp_req_source_len")
        self.tlp_req_source_dat      = Signal(data_width, name="tlp_req_source_dat")
        self.tlp_req_source_attr     = Signal(2, name="tlp_req_source_attr")
        self.tlp_req_source_at       = Signal(2, name="tlp_req_source_at")
        self.tlp_req_source_tag      = Signal(8, name="tlp_req_source_tag")

        # ----- DMA TLP Completion Input (Read responses from host) -----
        self.tlp_cpl_sink_valid  = Signal(name="tlp_cpl_sink_valid")
        self.tlp_cpl_sink_ready  = Signal(name="tlp_cpl_sink_ready")
        self.tlp_cpl_sink_first  = Signal(name="tlp_cpl_sink_first")
        self.tlp_cpl_sink_last   = Signal(name="tlp_cpl_sink_last")
        self.tlp_cpl_sink_dat    = Signal(data_width, name="tlp_cpl_sink_dat")
        self.tlp_cpl_sink_err    = Signal(name="tlp_cpl_sink_err")
        self.tlp_cpl_sink_end    = Signal(name="tlp_cpl_sink_end")
        self.tlp_cpl_sink_tag    = Signal(8, name="tlp_cpl_sink_tag")
        self.tlp_cpl_sink_len    = Signal(10, name="tlp_cpl_sink_len")

        # =====================================================================
        # Wire BAR1 handler signals
        # =====================================================================

        self.comb += [
            # Request sink
            self.handler.req_sink.valid.eq(self.bar1_req_sink_valid),
            self.bar1_req_sink_ready.eq(self.handler.req_sink.ready),
            self.handler.req_sink.first.eq(self.bar1_req_sink_first),
            self.handler.req_sink.last.eq(self.bar1_req_sink_last),
            self.handler.req_sink.we.eq(self.bar1_req_sink_we),
            self.handler.req_sink.adr.eq(self.bar1_req_sink_adr),
            self.handler.req_sink.len.eq(self.bar1_req_sink_len),
            self.handler.req_sink.req_id.eq(self.bar1_req_sink_req_id),
            self.handler.req_sink.tag.eq(self.bar1_req_sink_tag),
            self.handler.req_sink.dat.eq(self.bar1_req_sink_dat),
            self.handler.req_sink.first_be.eq(self.bar1_req_sink_first_be),
            self.handler.req_sink.last_be.eq(self.bar1_req_sink_last_be),

            # Completion source
            self.bar1_cpl_source_valid.eq(self.handler.cpl_source.valid),
            self.handler.cpl_source.ready.eq(self.bar1_cpl_source_ready),
            self.bar1_cpl_source_first.eq(self.handler.cpl_source.first),
            self.bar1_cpl_source_last.eq(self.handler.cpl_source.last),
            self.bar1_cpl_source_dat.eq(self.handler.cpl_source.dat),
            self.bar1_cpl_source_tag.eq(self.handler.cpl_source.tag),
            self.bar1_cpl_source_err.eq(self.handler.cpl_source.err),
        ]

        # =====================================================================
        # Wire DMA engine control signals
        # =====================================================================

        self.comb += [
            self.engine.trigger.eq(self.dma_trigger),
            self.engine.direction.eq(self.dma_direction),
            self.engine.no_snoop.eq(self.dma_no_snoop),
            self.engine.addr_type.eq(self.dma_addr_type),
            self.engine.bus_addr.eq(self.dma_bus_addr),
            self.engine.length.eq(self.dma_length),
            self.engine.offset.eq(self.dma_offset),

            self.dma_busy.eq(self.engine.busy),
            self.dma_status.eq(self.engine.status),
            self.dma_status_we.eq(self.engine.status_we),
        ]

        # =====================================================================
        # Wire DMA engine TLP interfaces
        # =====================================================================

        self.comb += [
            # TLP Request source (outgoing reads/writes)
            self.tlp_req_source_valid.eq(self.engine.source.valid),
            self.engine.source.ready.eq(self.tlp_req_source_ready),
            self.tlp_req_source_first.eq(self.engine.source.first),
            self.tlp_req_source_last.eq(self.engine.source.last),
            self.tlp_req_source_we.eq(self.engine.source.we),
            self.tlp_req_source_adr.eq(self.engine.source.adr),
            self.tlp_req_source_len.eq(self.engine.source.len),
            self.tlp_req_source_dat.eq(self.engine.source.dat),
            self.tlp_req_source_attr.eq(self.engine.source.attr),
            self.tlp_req_source_at.eq(self.engine.source.at),
            self.tlp_req_source_tag.eq(self.engine.source.tag),

            # TLP Completion sink (incoming read completions)
            self.engine.sink.valid.eq(self.tlp_cpl_sink_valid),
            self.tlp_cpl_sink_ready.eq(self.engine.sink.ready),
            self.engine.sink.first.eq(self.tlp_cpl_sink_first),
            self.engine.sink.last.eq(self.tlp_cpl_sink_last),
            self.engine.sink.dat.eq(self.tlp_cpl_sink_dat),
            self.engine.sink.err.eq(self.tlp_cpl_sink_err),
            self.engine.sink.end.eq(self.tlp_cpl_sink_end),
            self.engine.sink.tag.eq(self.tlp_cpl_sink_tag),
            self.engine.sink.len.eq(self.tlp_cpl_sink_len),
        ]


def generate_verilog():
    """Generate Verilog for Cocotb simulation."""
    import os
    from migen.fhdl.verilog import convert

    # Create testbench with small buffer for faster simulation
    tb = DMATestbench(data_width=64, buffer_size=1024)

    # Specify I/Os for the top-level module
    ios = set()

    # Clock and reset
    ios.add(tb.cd_sys.clk)
    ios.add(tb.cd_sys.rst)

    # BAR1 request interface
    ios.add(tb.bar1_req_sink_valid)
    ios.add(tb.bar1_req_sink_ready)
    ios.add(tb.bar1_req_sink_first)
    ios.add(tb.bar1_req_sink_last)
    ios.add(tb.bar1_req_sink_we)
    ios.add(tb.bar1_req_sink_adr)
    ios.add(tb.bar1_req_sink_len)
    ios.add(tb.bar1_req_sink_req_id)
    ios.add(tb.bar1_req_sink_tag)
    ios.add(tb.bar1_req_sink_dat)
    ios.add(tb.bar1_req_sink_first_be)
    ios.add(tb.bar1_req_sink_last_be)

    # BAR1 completion interface
    ios.add(tb.bar1_cpl_source_valid)
    ios.add(tb.bar1_cpl_source_ready)
    ios.add(tb.bar1_cpl_source_first)
    ios.add(tb.bar1_cpl_source_last)
    ios.add(tb.bar1_cpl_source_dat)
    ios.add(tb.bar1_cpl_source_tag)
    ios.add(tb.bar1_cpl_source_err)

    # DMA control interface
    ios.add(tb.dma_trigger)
    ios.add(tb.dma_direction)
    ios.add(tb.dma_no_snoop)
    ios.add(tb.dma_addr_type)
    ios.add(tb.dma_bus_addr)
    ios.add(tb.dma_length)
    ios.add(tb.dma_offset)

    # DMA status interface
    ios.add(tb.dma_busy)
    ios.add(tb.dma_status)
    ios.add(tb.dma_status_we)

    # DMA TLP request interface
    ios.add(tb.tlp_req_source_valid)
    ios.add(tb.tlp_req_source_ready)
    ios.add(tb.tlp_req_source_first)
    ios.add(tb.tlp_req_source_last)
    ios.add(tb.tlp_req_source_we)
    ios.add(tb.tlp_req_source_adr)
    ios.add(tb.tlp_req_source_len)
    ios.add(tb.tlp_req_source_dat)
    ios.add(tb.tlp_req_source_attr)
    ios.add(tb.tlp_req_source_at)
    ios.add(tb.tlp_req_source_tag)

    # DMA TLP completion interface
    ios.add(tb.tlp_cpl_sink_valid)
    ios.add(tb.tlp_cpl_sink_ready)
    ios.add(tb.tlp_cpl_sink_first)
    ios.add(tb.tlp_cpl_sink_last)
    ios.add(tb.tlp_cpl_sink_dat)
    ios.add(tb.tlp_cpl_sink_err)
    ios.add(tb.tlp_cpl_sink_end)
    ios.add(tb.tlp_cpl_sink_tag)
    ios.add(tb.tlp_cpl_sink_len)

    # Generate Verilog
    output = convert(tb, ios=ios, name="tb_dma")

    # Write Verilog to build directory
    build_dir = "build/sim"
    os.makedirs(build_dir, exist_ok=True)

    orig_dir = os.getcwd()
    os.chdir(build_dir)
    output.write("tb_dma.v")
    os.chdir(orig_dir)

    print(f"Generated {build_dir}/tb_dma.v")


if __name__ == "__main__":
    generate_verilog()
