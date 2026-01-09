#
# DMA Engine Cocotb Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Cocotb testbench for DMA engine and BAR1 buffer handler.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer


# =============================================================================
# TLP Request Source BFM (for BAR1 host access)
# =============================================================================

class TLPRequestSource:
    """
    Bus Functional Model for sending TLP requests to BAR1 buffer.
    Drives the bar1_req_sink interface with Memory Read/Write TLPs.
    """

    def __init__(self, dut, prefix="bar1_req_sink"):
        self.dut = dut
        self.prefix = prefix
        self.clk = dut.sys_clk
        self._get_signals()

    def _get_signals(self):
        """Get signal handles from DUT."""
        p = self.prefix
        self.valid = getattr(self.dut, f"{p}_valid")
        self.ready = getattr(self.dut, f"{p}_ready")
        self.first = getattr(self.dut, f"{p}_first")
        self.last = getattr(self.dut, f"{p}_last")
        self.we = getattr(self.dut, f"{p}_we")
        self.adr = getattr(self.dut, f"{p}_adr")
        self.len = getattr(self.dut, f"{p}_len")
        self.req_id = getattr(self.dut, f"{p}_req_id")
        self.tag = getattr(self.dut, f"{p}_tag")
        self.dat = getattr(self.dut, f"{p}_dat")
        self.first_be = getattr(self.dut, f"{p}_first_be")
        self.last_be = getattr(self.dut, f"{p}_last_be")

    async def reset(self):
        """Reset the interface."""
        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0
        self.adr.value = 0
        self.len.value = 0
        self.req_id.value = 0
        self.tag.value = 0
        self.dat.value = 0
        self.first_be.value = 0
        self.last_be.value = 0

    async def write64(self, addr, data, tag=0, req_id=0x0100):
        """Send a 64-bit Memory Write TLP."""
        await RisingEdge(self.clk)

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 1
        self.adr.value = addr
        self.len.value = 2
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = data
        self.first_be.value = 0xF
        self.last_be.value = 0xF

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0

    async def write32(self, addr, data, tag=0, req_id=0x0100):
        """Send a 32-bit Memory Write TLP."""
        if addr & 0x4:
            full_data = data << 32
            first_be = 0x0
            last_be = 0xF
        else:
            full_data = data
            first_be = 0xF
            last_be = 0x0

        await RisingEdge(self.clk)

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 1
        self.adr.value = addr
        self.len.value = 1 if last_be == 0 else 2
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = full_data
        self.first_be.value = first_be
        self.last_be.value = last_be

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0

    async def read(self, addr, length=2, tag=0, req_id=0x0100):
        """Send a Memory Read TLP."""
        await RisingEdge(self.clk)

        if length >= 2:
            first_be = 0xF
            last_be = 0xF
        else:
            first_be = 0xF
            last_be = 0x0

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 0
        self.adr.value = addr
        self.len.value = length
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = 0
        self.first_be.value = first_be
        self.last_be.value = last_be

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0


# =============================================================================
# TLP Completion Sink BFM (for BAR1 read completions)
# =============================================================================

class TLPCompletionSink:
    """
    Bus Functional Model for receiving TLP completions from BAR1.
    """

    def __init__(self, dut, prefix="bar1_cpl_source"):
        self.dut = dut
        self.prefix = prefix
        self.clk = dut.sys_clk
        self._get_signals()
        self.completions = []

    def _get_signals(self):
        """Get signal handles from DUT."""
        p = self.prefix
        self.valid = getattr(self.dut, f"{p}_valid")
        self.ready = getattr(self.dut, f"{p}_ready")
        self.first = getattr(self.dut, f"{p}_first")
        self.last = getattr(self.dut, f"{p}_last")
        self.dat = getattr(self.dut, f"{p}_dat")
        self.tag = getattr(self.dut, f"{p}_tag")
        self.err = getattr(self.dut, f"{p}_err")

    async def reset(self):
        """Reset the interface."""
        self.ready.value = 1
        self.completions = []

    async def wait_completion(self, timeout_cycles=100):
        """Wait for a completion."""
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.valid.value and self.ready.value:
                cpl = {
                    'data': int(self.dat.value),
                    'tag': int(self.tag.value),
                    'err': int(self.err.value),
                }
                self.completions.append(cpl)
                return cpl
        return None


# =============================================================================
# DMA TLP Request Sink BFM (captures DMA engine's TLP outputs)
# =============================================================================

class DMATLPRequestSink:
    """
    Bus Functional Model for capturing DMA engine's TLP requests.
    Monitors Memory Read and Write TLPs from the DMA engine.
    """

    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.sys_clk
        self._get_signals()
        self.requests = []

    def _get_signals(self):
        """Get signal handles from DUT."""
        self.valid = self.dut.tlp_req_source_valid
        self.ready = self.dut.tlp_req_source_ready
        self.first = self.dut.tlp_req_source_first
        self.last = self.dut.tlp_req_source_last
        self.we = self.dut.tlp_req_source_we
        self.adr = self.dut.tlp_req_source_adr
        self.len = self.dut.tlp_req_source_len
        self.dat = self.dut.tlp_req_source_dat
        self.attr = self.dut.tlp_req_source_attr
        self.at = self.dut.tlp_req_source_at
        self.tag = self.dut.tlp_req_source_tag

    async def reset(self):
        """Reset the interface - always ready to accept."""
        self.ready.value = 1
        self.requests = []

    async def wait_request(self, timeout_cycles=100):
        """Wait for a TLP request from DMA engine."""
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.valid.value and self.ready.value:
                req = {
                    'we': int(self.we.value),
                    'addr': int(self.adr.value),
                    'len': int(self.len.value),
                    'data': int(self.dat.value),
                    'attr': int(self.attr.value),
                    'at': int(self.at.value),
                    'tag': int(self.tag.value),
                    'first': int(self.first.value),
                    'last': int(self.last.value),
                }
                self.requests.append(req)
                return req
        return None


# =============================================================================
# DMA TLP Completion Source BFM (injects read completions to DMA engine)
# =============================================================================

class DMATLPCompletionSource:
    """
    Bus Functional Model for sending read completions to DMA engine.
    """

    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.sys_clk
        self._get_signals()

    def _get_signals(self):
        """Get signal handles from DUT."""
        self.valid = self.dut.tlp_cpl_sink_valid
        self.ready = self.dut.tlp_cpl_sink_ready
        self.first = self.dut.tlp_cpl_sink_first
        self.last = self.dut.tlp_cpl_sink_last
        self.dat = self.dut.tlp_cpl_sink_dat
        self.err = self.dut.tlp_cpl_sink_err
        self.end = self.dut.tlp_cpl_sink_end
        self.tag = self.dut.tlp_cpl_sink_tag
        self.len = self.dut.tlp_cpl_sink_len

    async def reset(self):
        """Reset the interface."""
        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.dat.value = 0
        self.err.value = 0
        self.end.value = 0
        self.tag.value = 0
        self.len.value = 0

    async def send_completion(self, data, tag, length=2, is_first=True, is_last=True,
                               is_end=True, error=False):
        """Send a completion TLP to DMA engine."""
        await RisingEdge(self.clk)

        self.valid.value = 1
        self.first.value = 1 if is_first else 0
        self.last.value = 1 if is_last else 0
        self.dat.value = data
        self.err.value = 1 if error else 0
        self.end.value = 1 if is_end else 0
        self.tag.value = tag
        self.len.value = length

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.end.value = 0


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    await ClockCycles(dut.sys_clk, 5)
    dut.sys_rst.value = 0
    await ClockCycles(dut.sys_clk, 5)


async def wait_dma_complete(dut, timeout_cycles=500):
    """Wait for DMA to complete."""
    for _ in range(timeout_cycles):
        await RisingEdge(dut.sys_clk)
        if dut.dma_status_we.value:
            return int(dut.dma_status.value)
    return None


# =============================================================================
# BAR1 Host Access Tests
# =============================================================================

@cocotb.test()
async def test_bar1_write_read(dut):
    """Test writing and reading BAR1 buffer via host TLPs."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    bar1_req = TLPRequestSource(dut, "bar1_req_sink")
    bar1_cpl = TLPCompletionSink(dut, "bar1_cpl_source")

    await reset_dut(dut)
    await bar1_req.reset()
    await bar1_cpl.reset()

    # Write 64-bit value to offset 0
    test_data = 0xDEADBEEFCAFEBABE
    dut._log.info(f"Writing 0x{test_data:016X} to offset 0")
    await bar1_req.write64(0x00, test_data, tag=1)

    await ClockCycles(dut.sys_clk, 5)

    # Read back
    dut._log.info("Reading from offset 0")
    await bar1_req.read(0x00, length=2, tag=2)
    cpl = await bar1_cpl.wait_completion()

    if cpl is None:
        raise AssertionError("Timeout waiting for read completion")

    dut._log.info(f"Read data: 0x{cpl['data']:016X}")

    if cpl['data'] != test_data:
        raise AssertionError(f"Data mismatch: got 0x{cpl['data']:016X}, expected 0x{test_data:016X}")

    dut._log.info("test_bar1_write_read PASSED")


@cocotb.test()
async def test_bar1_32bit_access(dut):
    """Test 32-bit aligned access to BAR1."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    bar1_req = TLPRequestSource(dut, "bar1_req_sink")
    bar1_cpl = TLPCompletionSink(dut, "bar1_cpl_source")

    await reset_dut(dut)
    await bar1_req.reset()
    await bar1_cpl.reset()

    # Write to lower DWORD (offset 0)
    dut._log.info("Writing 0x11223344 to lower DWORD (offset 0)")
    await bar1_req.write32(0x00, 0x11223344)

    # Write to upper DWORD (offset 4)
    dut._log.info("Writing 0x55667788 to upper DWORD (offset 4)")
    await bar1_req.write32(0x04, 0x55667788)

    await ClockCycles(dut.sys_clk, 5)

    # Read back full QWORD
    await bar1_req.read(0x00, length=2, tag=1)
    cpl = await bar1_cpl.wait_completion()

    if cpl is None:
        raise AssertionError("Timeout waiting for read completion")

    expected = (0x55667788 << 32) | 0x11223344
    dut._log.info(f"Read data: 0x{cpl['data']:016X}, expected: 0x{expected:016X}")

    if cpl['data'] != expected:
        raise AssertionError(f"Data mismatch: got 0x{cpl['data']:016X}, expected 0x{expected:016X}")

    dut._log.info("test_bar1_32bit_access PASSED")


# =============================================================================
# DMA Engine Tests
# =============================================================================

@cocotb.test()
async def test_basic_dma_read(dut):
    """Test single-beat DMA read (host → exerciser)."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    dma_req = DMATLPRequestSink(dut)
    dma_cpl = DMATLPCompletionSource(dut)

    await reset_dut(dut)
    await dma_req.reset()
    await dma_cpl.reset()

    # Configure DMA: read 8 bytes from host address 0x1000 to buffer offset 0
    dut.dma_bus_addr.value = 0x1000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 0  # Read from host
    dut.dma_no_snoop.value = 0
    dut.dma_addr_type.value = 0

    await ClockCycles(dut.sys_clk, 2)

    # Trigger DMA
    dut._log.info("Triggering DMA read")
    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Wait for read request
    req = await dma_req.wait_request()
    if req is None:
        raise AssertionError("Timeout waiting for DMA read request")

    dut._log.info(f"DMA read request: addr=0x{req['addr']:X}, len={req['len']}, we={req['we']}")

    assert req['we'] == 0, "Expected read request (we=0)"
    assert req['addr'] == 0x1000, f"Wrong address: {req['addr']}"
    assert req['len'] == 2, f"Wrong length: {req['len']}"  # 8 bytes = 2 DWORDs

    # Send completion with test data
    test_data = 0xABCDEF0123456789
    await dma_cpl.send_completion(data=test_data, tag=req['tag'], length=2)

    # Wait for DMA complete
    status = await wait_dma_complete(dut)
    assert status == 0, f"DMA failed with status {status}"

    dut._log.info("test_basic_dma_read PASSED")


@cocotb.test()
async def test_basic_dma_write(dut):
    """Test single-beat DMA write (exerciser → host)."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    bar1_req = TLPRequestSource(dut, "bar1_req_sink")
    bar1_cpl = TLPCompletionSink(dut, "bar1_cpl_source")
    dma_req = DMATLPRequestSink(dut)

    await reset_dut(dut)
    await bar1_req.reset()
    await bar1_cpl.reset()
    await dma_req.reset()

    # Pre-load buffer via BAR1
    test_data = 0x123456789ABCDEF0
    dut._log.info(f"Pre-loading buffer with 0x{test_data:016X}")
    await bar1_req.write64(0x00, test_data, tag=1)
    await ClockCycles(dut.sys_clk, 5)

    # Configure DMA: write 8 bytes from buffer offset 0 to host address 0x2000
    dut.dma_bus_addr.value = 0x2000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 1  # Write to host
    dut.dma_no_snoop.value = 0
    dut.dma_addr_type.value = 0

    await ClockCycles(dut.sys_clk, 2)

    # Trigger DMA
    dut._log.info("Triggering DMA write")
    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Wait for write request
    req = await dma_req.wait_request()
    if req is None:
        raise AssertionError("Timeout waiting for DMA write request")

    dut._log.info(f"DMA write request: addr=0x{req['addr']:X}, len={req['len']}, data=0x{req['data']:016X}")

    assert req['we'] == 1, "Expected write request (we=1)"
    assert req['addr'] == 0x2000, f"Wrong address: {req['addr']}"
    assert req['data'] == test_data, f"Wrong data: 0x{req['data']:016X}"

    # Wait for DMA complete
    status = await wait_dma_complete(dut)
    assert status == 0, f"DMA failed with status {status}"

    dut._log.info("test_basic_dma_write PASSED")


@cocotb.test()
async def test_no_snoop_attribute(dut):
    """Test No-Snoop TLP attribute is set correctly."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    dma_req = DMATLPRequestSink(dut)
    dma_cpl = DMATLPCompletionSource(dut)

    await reset_dut(dut)
    await dma_req.reset()
    await dma_cpl.reset()

    # Configure DMA with No-Snoop enabled
    dut.dma_bus_addr.value = 0x3000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 0
    dut.dma_no_snoop.value = 1  # Enable No-Snoop
    dut.dma_addr_type.value = 0

    await ClockCycles(dut.sys_clk, 2)

    # Trigger DMA
    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Wait for read request
    req = await dma_req.wait_request()
    if req is None:
        raise AssertionError("Timeout waiting for DMA request")

    dut._log.info(f"DMA request attr=0b{req['attr']:02b}")

    # Check No-Snoop bit (attr[0])
    assert (req['attr'] & 0x1) == 1, f"No-Snoop bit not set: attr={req['attr']}"

    # Send completion
    await dma_cpl.send_completion(data=0, tag=req['tag'], length=2)
    await wait_dma_complete(dut)

    dut._log.info("test_no_snoop_attribute PASSED")


@cocotb.test()
async def test_address_type(dut):
    """Test Address Type TLP field for ATS."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    dma_req = DMATLPRequestSink(dut)
    dma_cpl = DMATLPCompletionSource(dut)

    await reset_dut(dut)
    await dma_req.reset()
    await dma_cpl.reset()

    # Configure DMA with Address Type = Translated (0b10)
    dut.dma_bus_addr.value = 0x4000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 0
    dut.dma_no_snoop.value = 0
    dut.dma_addr_type.value = 0b10  # Translated

    await ClockCycles(dut.sys_clk, 2)

    # Trigger DMA
    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Wait for read request
    req = await dma_req.wait_request()
    if req is None:
        raise AssertionError("Timeout waiting for DMA request")

    dut._log.info(f"DMA request at=0b{req['at']:02b}")

    # Check Address Type field
    assert req['at'] == 0b10, f"Wrong Address Type: at={req['at']}, expected 0b10"

    # Send completion
    await dma_cpl.send_completion(data=0, tag=req['tag'], length=2)
    await wait_dma_complete(dut)

    dut._log.info("test_address_type PASSED")


@cocotb.test()
async def test_completion_error(dut):
    """Test handling of completion with error."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    dma_req = DMATLPRequestSink(dut)
    dma_cpl = DMATLPCompletionSource(dut)

    await reset_dut(dut)
    await dma_req.reset()
    await dma_cpl.reset()

    # Configure DMA
    dut.dma_bus_addr.value = 0x5000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 0
    dut.dma_no_snoop.value = 0
    dut.dma_addr_type.value = 0

    await ClockCycles(dut.sys_clk, 2)

    # Trigger DMA
    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Wait for read request
    req = await dma_req.wait_request()
    if req is None:
        raise AssertionError("Timeout waiting for DMA request")

    # Send completion with error
    dut._log.info("Sending completion with error")
    await dma_cpl.send_completion(data=0, tag=req['tag'], length=0, error=True)

    # Wait for DMA complete
    status = await wait_dma_complete(dut)
    dut._log.info(f"DMA status = {status}")

    assert status == 0b01, f"Expected error status (0b01), got {status}"

    dut._log.info("test_completion_error PASSED")


# =============================================================================
# Integration Tests
# =============================================================================

@cocotb.test()
async def test_bar1_preload_dma_write(dut):
    """Test host writes to BAR1, then DMA writes to host."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    bar1_req = TLPRequestSource(dut, "bar1_req_sink")
    bar1_cpl = TLPCompletionSink(dut, "bar1_cpl_source")
    dma_req = DMATLPRequestSink(dut)

    await reset_dut(dut)
    await bar1_req.reset()
    await bar1_cpl.reset()
    await dma_req.reset()

    # Write multiple locations via BAR1
    test_data = [0x1111111111111111, 0x2222222222222222]

    for i, data in enumerate(test_data):
        dut._log.info(f"Writing 0x{data:016X} to BAR1 offset {i*8}")
        await bar1_req.write64(i * 8, data, tag=i)

    await ClockCycles(dut.sys_clk, 5)

    # DMA write first 8 bytes to host
    dut.dma_bus_addr.value = 0x6000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 1

    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Verify DMA write contains correct data
    req = await dma_req.wait_request()
    assert req is not None, "Timeout waiting for DMA write"
    assert req['data'] == test_data[0], f"Wrong data: 0x{req['data']:016X}"

    status = await wait_dma_complete(dut)
    assert status == 0

    dut._log.info("test_bar1_preload_dma_write PASSED")


@cocotb.test()
async def test_dma_read_bar1_verify(dut):
    """Test DMA reads from host, then host reads BAR1 to verify."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    bar1_req = TLPRequestSource(dut, "bar1_req_sink")
    bar1_cpl = TLPCompletionSink(dut, "bar1_cpl_source")
    dma_req = DMATLPRequestSink(dut)
    dma_cpl = DMATLPCompletionSource(dut)

    await reset_dut(dut)
    await bar1_req.reset()
    await bar1_cpl.reset()
    await dma_req.reset()
    await dma_cpl.reset()

    # Configure DMA read
    dut.dma_bus_addr.value = 0x7000
    dut.dma_length.value = 8
    dut.dma_offset.value = 0
    dut.dma_direction.value = 0  # Read from host

    dut.dma_trigger.value = 1
    await RisingEdge(dut.sys_clk)
    dut.dma_trigger.value = 0

    # Wait for read request
    req = await dma_req.wait_request()
    assert req is not None

    # Send completion with test data
    test_data = 0xFEDCBA9876543210
    await dma_cpl.send_completion(data=test_data, tag=req['tag'], length=2)

    status = await wait_dma_complete(dut)
    assert status == 0

    await ClockCycles(dut.sys_clk, 5)

    # Read BAR1 to verify data was stored
    dut._log.info("Reading BAR1 to verify DMA data")
    await bar1_req.read(0x00, length=2, tag=10)
    cpl = await bar1_cpl.wait_completion()

    assert cpl is not None, "Timeout reading BAR1"
    dut._log.info(f"BAR1 data: 0x{cpl['data']:016X}")

    assert cpl['data'] == test_data, f"Data mismatch: got 0x{cpl['data']:016X}, expected 0x{test_data:016X}"

    dut._log.info("test_dma_read_bar1_verify PASSED")
