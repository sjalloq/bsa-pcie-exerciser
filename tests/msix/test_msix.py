#
# MSI-X Cocotb Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Cocotb testbench for MSI-X subsystem.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer


# =============================================================================
# TLP Request Source BFM
# =============================================================================

class TLPRequestSource:
    """
    Bus Functional Model for sending TLP requests to MSI-X table/PBA.

    Drives the req_sink interface with Memory Read/Write TLPs.
    """

    def __init__(self, dut, prefix):
        """
        Args:
            dut: Cocotb DUT handle
            prefix: Signal prefix (e.g., "bar2_req_sink" or "bar5_req_sink")
        """
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

    async def write(self, addr, data, first_be=0xF, last_be=0xF, tag=0, req_id=0x0100):
        """
        Send a Memory Write TLP.

        Args:
            addr: Byte address within BAR
            data: 64-bit data value
            first_be: Byte enables for first DWORD (4 bits)
            last_be: Byte enables for last DWORD (4 bits)
            tag: TLP tag
            req_id: Requester ID
        """
        await RisingEdge(self.clk)

        # Calculate length from byte enables
        length = 1 if last_be == 0 else 2

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 1
        self.adr.value = addr
        self.len.value = length
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = data
        self.first_be.value = first_be
        self.last_be.value = last_be

        # Wait for ready
        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        # Deassert all signals
        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0
        self.first_be.value = 0
        self.last_be.value = 0

    async def write32(self, addr, data, tag=0, req_id=0x0100):
        """
        Send a 32-bit Memory Write TLP.

        Args:
            addr: Byte address (DWORD aligned)
            data: 32-bit data value
            tag: TLP tag
            req_id: Requester ID
        """
        # Determine which DWORD within QWORD based on addr[2]
        if addr & 0x4:
            # Upper DWORD - data in upper 32 bits, last_be active
            full_data = data << 32
            first_be = 0x0
            last_be = 0xF
        else:
            # Lower DWORD - data in lower 32 bits, first_be active
            full_data = data
            first_be = 0xF
            last_be = 0x0

        await self.write(addr, full_data, first_be=first_be, last_be=last_be, tag=tag, req_id=req_id)

    async def read(self, addr, length=2, tag=0, req_id=0x0100):
        """
        Send a Memory Read TLP.

        Args:
            addr: Byte address within BAR
            length: Length in DWORDs (1 or 2)
            tag: TLP tag
            req_id: Requester ID
        """
        await RisingEdge(self.clk)

        # Set byte enables based on length
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

        # Wait for ready
        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0


# =============================================================================
# TLP Completion Sink BFM
# =============================================================================

class TLPCompletionSink:
    """
    Bus Functional Model for receiving TLP completions.

    Monitors the cpl_source interface for read completions.
    """

    def __init__(self, dut, prefix):
        """
        Args:
            dut: Cocotb DUT handle
            prefix: Signal prefix (e.g., "bar2_cpl_source")
        """
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
        """
        Wait for a completion.

        Returns:
            dict with completion fields, or None on timeout
        """
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.valid.value and self.ready.value:
                cpl = {
                    'data': int(self.dat.value),
                    'tag': int(self.tag.value),
                    'err': int(self.err.value),
                    'first': int(self.first.value),
                    'last': int(self.last.value),
                }
                self.completions.append(cpl)
                return cpl
        return None


# =============================================================================
# MSI-X TLP Sink BFM
# =============================================================================

class MSIXTLPSink:
    """
    Bus Functional Model for capturing MSI-X Memory Write TLPs.

    Monitors the msi_source interface for interrupt TLPs.
    """

    def __init__(self, dut):
        self.dut = dut
        self.clk = dut.sys_clk
        self._get_signals()
        self.tlps = []

    def _get_signals(self):
        """Get signal handles from DUT."""
        self.valid = self.dut.msi_source_valid
        self.ready = self.dut.msi_source_ready
        self.adr = self.dut.msi_source_adr
        self.dat = self.dut.msi_source_dat
        self.we = self.dut.msi_source_we

    async def reset(self):
        """Reset the interface - always ready to accept."""
        self.ready.value = 1
        self.tlps = []

    async def wait_tlp(self, timeout_cycles=100):
        """
        Wait for an MSI-X TLP.

        Returns:
            dict with TLP fields, or None on timeout
        """
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.valid.value and self.ready.value:
                tlp = {
                    'addr': int(self.adr.value),
                    'data': int(self.dat.value),
                    'we': int(self.we.value),
                }
                self.tlps.append(tlp)
                return tlp
        return None


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    await ClockCycles(dut.sys_clk, 5)
    dut.sys_rst.value = 0
    await ClockCycles(dut.sys_clk, 5)


# =============================================================================
# Test Cases
# =============================================================================

@cocotb.test()
async def test_table_write_read(dut):
    """Test writing and reading MSI-X table entries."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    table_cpl = TLPCompletionSink(dut, "bar2_cpl_source")

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await table_cpl.reset()

    # Write vector 0: addr=0x00000000_FEE00000 (32-bit MSI address), data=0x12345678, unmask
    # Vector 0 at offset 0x00:
    #   QWORD 0 (offset 0x00): {addr_hi, addr_lo}
    #   QWORD 1 (offset 0x08): {control, msg_data}

    dut._log.info("Writing vector 0 address low (0x00)")
    await table_req.write32(0x00, 0xFEE01234)  # addr_lo (32-bit MSI address)

    dut._log.info("Writing vector 0 address high (0x04)")
    await table_req.write32(0x04, 0x00000000)  # addr_hi (0 for 32-bit addressing)

    dut._log.info("Writing vector 0 message data (0x08)")
    await table_req.write32(0x08, 0x12345678)  # msg_data

    dut._log.info("Writing vector 0 control - unmask (0x0C)")
    await table_req.write32(0x0C, 0x00000000)  # control (unmask)

    await ClockCycles(dut.sys_clk, 5)

    # Read back and verify
    dut._log.info("Reading vector 0 QWORD 0")
    await table_req.read(0x00, length=2, tag=1)
    cpl = await table_cpl.wait_completion()

    if cpl is None:
        raise AssertionError("Timeout waiting for read completion")

    dut._log.info(f"Read completion: data=0x{cpl['data']:016X}")

    # Expected: {addr_hi, addr_lo} = {0x00000000, 0xFEE01234}
    expected = (0x00000000 << 32) | 0xFEE01234
    if cpl['data'] != expected:
        raise AssertionError(f"QWORD 0 mismatch: got 0x{cpl['data']:016X}, expected 0x{expected:016X}")

    dut._log.info("test_table_write_read PASSED")


@cocotb.test()
async def test_software_trigger(dut):
    """Test software-triggered MSI-X interrupt."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program vector 0 with 32-bit MSI address
    dut._log.info("Programming vector 0")
    await table_req.write32(0x00, 0xFEE00000)  # addr_lo (32-bit MSI address)
    await table_req.write32(0x04, 0x00000000)  # addr_hi (0 for 32-bit addressing)
    await table_req.write32(0x08, 0xDEADBEEF)  # msg_data
    await table_req.write32(0x0C, 0x00000000)  # unmask

    await ClockCycles(dut.sys_clk, 5)

    # Trigger vector 0
    dut._log.info("Triggering vector 0")
    dut.sw_vector.value = 0
    dut.sw_valid.value = 1
    await RisingEdge(dut.sys_clk)
    dut.sw_valid.value = 0

    # Wait for MSI-X TLP
    tlp = await msi_sink.wait_tlp()

    if tlp is None:
        raise AssertionError("Timeout waiting for MSI-X TLP")

    dut._log.info(f"MSI-X TLP: addr=0x{tlp['addr']:08X}, data=0x{tlp['data']:08X}")

    # Verify TLP - 32-bit address in lower bits
    expected_addr = 0xFEE00000
    if tlp['addr'] != expected_addr:
        raise AssertionError(f"Address mismatch: got 0x{tlp['addr']:08X}, expected 0x{expected_addr:08X}")

    if tlp['data'] != 0xDEADBEEF:
        raise AssertionError(f"Data mismatch: got 0x{tlp['data']:08X}, expected 0xDEADBEEF")

    dut._log.info("test_software_trigger PASSED")


@cocotb.test()
async def test_masked_vector(dut):
    """Test that masked vectors set pending bit instead of generating TLP."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    pba_req = TLPRequestSource(dut, "bar5_req_sink")
    pba_cpl = TLPCompletionSink(dut, "bar5_cpl_source")
    msi_sink = MSIXTLPSink(dut)

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await pba_req.reset()
    await pba_cpl.reset()
    await msi_sink.reset()

    # Program vector 1 with mask bit SET
    dut._log.info("Programming vector 1 (masked)")
    await table_req.write32(0x10, 0x11110000)  # addr_lo (vector 1 at offset 0x10)
    await table_req.write32(0x14, 0xFEE00000)  # addr_hi
    await table_req.write32(0x18, 0x11111111)  # msg_data
    await table_req.write32(0x1C, 0x00000001)  # control: MASKED

    await ClockCycles(dut.sys_clk, 5)

    # Trigger vector 1
    dut._log.info("Triggering vector 1 (should NOT generate TLP)")
    dut.sw_vector.value = 1
    dut.sw_valid.value = 1
    await RisingEdge(dut.sys_clk)
    dut.sw_valid.value = 0

    # Wait a few cycles - should NOT get TLP
    await ClockCycles(dut.sys_clk, 20)

    if len(msi_sink.tlps) > 0:
        raise AssertionError("Received TLP for masked vector!")

    # Read PBA - vector 1 should be pending (bit 1 of QWORD 0)
    dut._log.info("Reading PBA")
    await pba_req.read(0x00, length=2, tag=2)
    cpl = await pba_cpl.wait_completion()

    if cpl is None:
        raise AssertionError("Timeout waiting for PBA read completion")

    dut._log.info(f"PBA read: 0x{cpl['data']:016X}")

    # Check bit 1 is set
    if not (cpl['data'] & 0x2):
        raise AssertionError(f"Pending bit not set for vector 1: PBA=0x{cpl['data']:016X}")

    dut._log.info("test_masked_vector PASSED")


@cocotb.test()
async def test_pba_read_only(dut):
    """Test that PBA ignores writes."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    pba_req = TLPRequestSource(dut, "bar5_req_sink")
    pba_cpl = TLPCompletionSink(dut, "bar5_cpl_source")

    # Reset
    await reset_dut(dut)
    await pba_req.reset()
    await pba_cpl.reset()

    # Read PBA initial state
    await pba_req.read(0x00, length=2, tag=1)
    cpl1 = await pba_cpl.wait_completion()
    initial_value = cpl1['data']
    dut._log.info(f"Initial PBA: 0x{initial_value:016X}")

    # Try to write to PBA (should be ignored)
    dut._log.info("Writing to PBA (should be ignored)")
    await pba_req.write(0x00, 0xFFFFFFFFFFFFFFFF, first_be=0xF, last_be=0xF)

    await ClockCycles(dut.sys_clk, 5)

    # Read back - should be unchanged
    await pba_req.read(0x00, length=2, tag=2)
    cpl2 = await pba_cpl.wait_completion()

    if cpl2['data'] != initial_value:
        raise AssertionError(f"PBA was modified by write! Before=0x{initial_value:016X}, After=0x{cpl2['data']:016X}")

    dut._log.info("test_pba_read_only PASSED")


@cocotb.test()
async def test_backpressure(dut):
    """Test controller stalls correctly when msi_source_ready=0."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program vector 0
    dut._log.info("Programming vector 0")
    await table_req.write32(0x00, 0xFEE00000)  # addr_lo
    await table_req.write32(0x04, 0x00000000)  # addr_hi
    await table_req.write32(0x08, 0x00000042)  # msg_data
    await table_req.write32(0x0C, 0x00000000)  # unmask

    await ClockCycles(dut.sys_clk, 5)

    # Deassert ready (apply backpressure)
    dut._log.info("Applying backpressure (msi_source_ready=0)")
    dut.msi_source_ready.value = 0

    # Trigger vector 0
    dut.sw_vector.value = 0
    dut.sw_valid.value = 1
    await RisingEdge(dut.sys_clk)
    dut.sw_valid.value = 0

    # Wait - should see valid asserted but stuck
    await ClockCycles(dut.sys_clk, 20)

    if dut.msi_source_valid.value != 1:
        raise AssertionError("Expected msi_source_valid=1 during backpressure")

    if dut.busy.value != 1:
        raise AssertionError("Expected busy=1 during backpressure")

    dut._log.info("Controller stalled correctly, releasing backpressure")

    # Release backpressure
    dut.msi_source_ready.value = 1
    await ClockCycles(dut.sys_clk, 2)

    # Should complete
    if dut.busy.value != 0:
        raise AssertionError("Expected busy=0 after backpressure released")

    dut._log.info("test_backpressure PASSED")


@cocotb.test()
async def test_back_to_back_triggers(dut):
    """Test back-to-back software triggers."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program vectors 0, 1, 2
    for v in range(3):
        offset = v * 16
        await table_req.write32(offset + 0x00, 0xFEE00000)  # addr_lo
        await table_req.write32(offset + 0x04, 0x00000000)  # addr_hi
        await table_req.write32(offset + 0x08, v)          # msg_data = vector number
        await table_req.write32(offset + 0x0C, 0x00000000)  # unmask

    await ClockCycles(dut.sys_clk, 5)

    # Fire triggers one after another, waiting for each to complete
    received = []
    for v in range(3):
        dut._log.info(f"Triggering vector {v}")
        dut.sw_vector.value = v
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        # Wait for TLP
        tlp = await msi_sink.wait_tlp(timeout_cycles=50)
        if tlp is None:
            raise AssertionError(f"No TLP received for vector {v}")
        received.append(tlp['data'])
        dut._log.info(f"Received TLP with data=0x{tlp['data']:08X}")

    # Verify we got all three in order
    if received != [0, 1, 2]:
        raise AssertionError(f"Wrong sequence: expected [0, 1, 2], got {received}")

    dut._log.info("test_back_to_back_triggers PASSED")


@cocotb.test()
async def test_32bit_read_dword_select(dut):
    """Test 32-bit read returns correct DWORD based on addr[2]."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    table_cpl = TLPCompletionSink(dut, "bar2_cpl_source")

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await table_cpl.reset()

    # Write known pattern to vector 0
    # QWORD 0: addr = 0xDEADBEEF_12345678
    # QWORD 1: {control, msg_data} = {0x00000000, 0xCAFEBABE}
    dut._log.info("Writing test pattern to vector 0")
    await table_req.write32(0x00, 0x12345678)  # addr_lo
    await table_req.write32(0x04, 0xDEADBEEF)  # addr_hi
    await table_req.write32(0x08, 0xCAFEBABE)  # msg_data
    await table_req.write32(0x0C, 0x00000000)  # control (unmask)

    await ClockCycles(dut.sys_clk, 5)

    # Read full QWORD 0 and verify
    dut._log.info("Reading QWORD 0 (64-bit)")
    await table_req.read(0x00, length=2, tag=1)
    cpl = await table_cpl.wait_completion()

    expected_qword0 = (0xDEADBEEF << 32) | 0x12345678
    if cpl['data'] != expected_qword0:
        raise AssertionError(f"QWORD 0 mismatch: got 0x{cpl['data']:016X}, expected 0x{expected_qword0:016X}")

    dut._log.info(f"QWORD 0 correct: 0x{cpl['data']:016X}")

    # Read full QWORD 1 and verify
    dut._log.info("Reading QWORD 1 (64-bit)")
    await table_req.read(0x08, length=2, tag=2)
    cpl = await table_cpl.wait_completion()

    expected_qword1 = (0x00000000 << 32) | 0xCAFEBABE
    if cpl['data'] != expected_qword1:
        raise AssertionError(f"QWORD 1 mismatch: got 0x{cpl['data']:016X}, expected 0x{expected_qword1:016X}")

    dut._log.info(f"QWORD 1 correct: 0x{cpl['data']:016X}")

    dut._log.info("test_32bit_read_dword_select PASSED")


@cocotb.test()
async def test_multiple_vectors(dut):
    """Test triggering different vectors produces correct TLPs."""

    # Start clock
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Create BFMs
    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    # Reset
    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program vectors 0, 5, 10 with different addresses and data
    test_vectors = [
        (0,  0xFEE00000, 0x00000000),
        (5,  0xFEE00100, 0x00000055),
        (10, 0xFEE00200, 0x000000AA),
    ]

    for vec_num, addr, data in test_vectors:
        offset = vec_num * 16
        await table_req.write32(offset + 0x00, addr)        # addr_lo
        await table_req.write32(offset + 0x04, 0x00000000)  # addr_hi
        await table_req.write32(offset + 0x08, data)        # msg_data
        await table_req.write32(offset + 0x0C, 0x00000000)  # unmask

    await ClockCycles(dut.sys_clk, 5)

    # Trigger each vector and verify TLP
    for vec_num, expected_addr, expected_data in test_vectors:
        dut._log.info(f"Triggering vector {vec_num}")
        dut.sw_vector.value = vec_num
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        tlp = await msi_sink.wait_tlp(timeout_cycles=50)

        if tlp is None:
            raise AssertionError(f"No TLP for vector {vec_num}")

        if tlp['addr'] != expected_addr:
            raise AssertionError(f"Vector {vec_num}: addr mismatch, got 0x{tlp['addr']:08X}, expected 0x{expected_addr:08X}")

        if tlp['data'] != expected_data:
            raise AssertionError(f"Vector {vec_num}: data mismatch, got 0x{tlp['data']:08X}, expected 0x{expected_data:08X}")

        dut._log.info(f"Vector {vec_num} correct: addr=0x{tlp['addr']:08X}, data=0x{tlp['data']:08X}")

    dut._log.info("test_multiple_vectors PASSED")


@cocotb.test()
async def test_all_vectors(dut):
    """Test programming and triggering all 16 vectors."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program all 16 vectors with unique addresses and data
    for vec in range(16):
        offset = vec * 16
        addr = 0xFEE00000 + (vec << 4)
        data = 0x10000000 + vec
        await table_req.write32(offset + 0x00, addr)
        await table_req.write32(offset + 0x04, 0x00000000)
        await table_req.write32(offset + 0x08, data)
        await table_req.write32(offset + 0x0C, 0x00000000)  # unmask

    await ClockCycles(dut.sys_clk, 5)

    # Trigger and verify each vector
    for vec in range(16):
        expected_addr = 0xFEE00000 + (vec << 4)
        expected_data = 0x10000000 + vec

        dut.sw_vector.value = vec
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        tlp = await msi_sink.wait_tlp(timeout_cycles=50)

        if tlp is None:
            raise AssertionError(f"No TLP for vector {vec}")
        if tlp['addr'] != expected_addr:
            raise AssertionError(f"Vector {vec}: addr 0x{tlp['addr']:08X} != expected 0x{expected_addr:08X}")
        if tlp['data'] != expected_data:
            raise AssertionError(f"Vector {vec}: data 0x{tlp['data']:08X} != expected 0x{expected_data:08X}")

    dut._log.info("test_all_vectors PASSED - all 16 vectors correct")


@cocotb.test()
async def test_write_isolation(dut):
    """Test that writes to one vector don't corrupt adjacent vectors."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    table_cpl = TLPCompletionSink(dut, "bar2_cpl_source")

    await reset_dut(dut)
    await table_req.reset()
    await table_cpl.reset()

    # Write distinct patterns to vectors 0, 1, 2
    patterns = [
        (0, 0xAAAAAAAA, 0x11111111),
        (1, 0xBBBBBBBB, 0x22222222),
        (2, 0xCCCCCCCC, 0x33333333),
    ]

    for vec, addr_pattern, data_pattern in patterns:
        offset = vec * 16
        await table_req.write32(offset + 0x00, addr_pattern)
        await table_req.write32(offset + 0x04, addr_pattern ^ 0xFFFFFFFF)
        await table_req.write32(offset + 0x08, data_pattern)
        await table_req.write32(offset + 0x0C, data_pattern ^ 0xFFFFFFFF)

    await ClockCycles(dut.sys_clk, 5)

    # Verify each vector still has correct data (wasn't corrupted by neighbors)
    for vec, addr_pattern, data_pattern in patterns:
        offset = vec * 16

        # Read QWORD 0 (addr_lo, addr_hi)
        await table_req.read(offset, length=2)
        cpl = await table_cpl.wait_completion()
        expected_q0 = ((addr_pattern ^ 0xFFFFFFFF) << 32) | addr_pattern
        if cpl['data'] != expected_q0:
            raise AssertionError(f"Vector {vec} QWORD0: 0x{cpl['data']:016X} != 0x{expected_q0:016X}")

        # Read QWORD 1 (msg_data, control)
        await table_req.read(offset + 8, length=2)
        cpl = await table_cpl.wait_completion()
        expected_q1 = ((data_pattern ^ 0xFFFFFFFF) << 32) | data_pattern
        if cpl['data'] != expected_q1:
            raise AssertionError(f"Vector {vec} QWORD1: 0x{cpl['data']:016X} != 0x{expected_q1:016X}")

    dut._log.info("test_write_isolation PASSED - no cross-vector corruption")


@cocotb.test()
async def test_random_access_pattern(dut):
    """Test non-sequential access patterns to vectors."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program vectors in non-sequential order
    access_order = [7, 2, 15, 0, 9, 4, 11, 6, 13, 1, 8, 3, 14, 5, 12, 10]

    for vec in access_order:
        offset = vec * 16
        addr = 0xFEE00000 | (vec << 8)
        data = vec * 0x11111111
        await table_req.write32(offset + 0x00, addr)
        await table_req.write32(offset + 0x04, 0x00000000)
        await table_req.write32(offset + 0x08, data)
        await table_req.write32(offset + 0x0C, 0x00000000)

    await ClockCycles(dut.sys_clk, 5)

    # Trigger in a different non-sequential order
    trigger_order = [12, 3, 8, 15, 1, 6, 10, 4, 14, 0, 7, 11, 2, 9, 5, 13]

    for vec in trigger_order:
        expected_addr = 0xFEE00000 | (vec << 8)
        expected_data = vec * 0x11111111

        dut.sw_vector.value = vec
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        tlp = await msi_sink.wait_tlp(timeout_cycles=50)

        if tlp is None:
            raise AssertionError(f"No TLP for vector {vec}")
        if tlp['addr'] != expected_addr or tlp['data'] != expected_data:
            raise AssertionError(f"Vector {vec} mismatch")

    dut._log.info("test_random_access_pattern PASSED")


@cocotb.test()
async def test_64bit_write_read(dut):
    """Test 64-bit (QWORD) writes and reads."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    table_cpl = TLPCompletionSink(dut, "bar2_cpl_source")

    await reset_dut(dut)
    await table_req.reset()
    await table_cpl.reset()

    # Test various QWORD patterns
    test_patterns = [
        0x0000000000000000,
        0xFFFFFFFFFFFFFFFF,
        0xAAAAAAAAAAAAAAAA,
        0x5555555555555555,
        0x123456789ABCDEF0,
        0xFEDCBA9876543210,
    ]

    for i, pattern in enumerate(test_patterns):
        offset = i * 16  # Each vector is 16 bytes

        # Write QWORD with both byte enables active
        await table_req.write(offset, pattern, first_be=0xF, last_be=0xF)
        await ClockCycles(dut.sys_clk, 2)

        # Read back
        await table_req.read(offset, length=2)
        cpl = await table_cpl.wait_completion()

        if cpl['data'] != pattern:
            raise AssertionError(f"Pattern {i}: wrote 0x{pattern:016X}, read 0x{cpl['data']:016X}")

    dut._log.info("test_64bit_write_read PASSED")


@cocotb.test()
async def test_data_patterns(dut):
    """Test various data patterns to catch stuck bits or inversions."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Walking 1s pattern for msg_data
    for bit in range(32):
        data = 1 << bit
        await table_req.write32(0x00, 0xFEE00000)
        await table_req.write32(0x04, 0x00000000)
        await table_req.write32(0x08, data)
        await table_req.write32(0x0C, 0x00000000)

        await ClockCycles(dut.sys_clk, 2)

        dut.sw_vector.value = 0
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        tlp = await msi_sink.wait_tlp(timeout_cycles=50)

        if tlp is None or tlp['data'] != data:
            raise AssertionError(f"Walking 1 bit {bit}: expected 0x{data:08X}, got {tlp}")

    # Walking 0s pattern
    for bit in range(32):
        data = 0xFFFFFFFF ^ (1 << bit)
        await table_req.write32(0x08, data)

        await ClockCycles(dut.sys_clk, 2)

        dut.sw_vector.value = 0
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        tlp = await msi_sink.wait_tlp(timeout_cycles=50)

        if tlp is None or tlp['data'] != data:
            raise AssertionError(f"Walking 0 bit {bit}: expected 0x{data:08X}, got {tlp}")

    dut._log.info("test_data_patterns PASSED - all 64 bit patterns correct")


@cocotb.test()
async def test_rapid_triggers(dut):
    """Test rapid consecutive triggers without waiting for TLP completion."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    msi_sink = MSIXTLPSink(dut)

    await reset_dut(dut)
    await table_req.reset()
    await msi_sink.reset()

    # Program 8 vectors
    for vec in range(8):
        offset = vec * 16
        await table_req.write32(offset + 0x00, 0xFEE00000 + vec)
        await table_req.write32(offset + 0x04, 0x00000000)
        await table_req.write32(offset + 0x08, vec)
        await table_req.write32(offset + 0x0C, 0x00000000)

    await ClockCycles(dut.sys_clk, 5)

    # Fire triggers as fast as possible and collect TLPs
    expected_tlps = []
    for vec in range(8):
        expected_tlps.append((0xFEE00000 + vec, vec))

        dut.sw_vector.value = vec
        dut.sw_valid.value = 1
        await RisingEdge(dut.sys_clk)
        dut.sw_valid.value = 0

        # Wait for TLP before next trigger (controller must return to IDLE)
        tlp = await msi_sink.wait_tlp(timeout_cycles=50)
        if tlp is None:
            raise AssertionError(f"No TLP for rapid trigger {vec}")

        exp_addr, exp_data = expected_tlps[vec]
        if tlp['addr'] != exp_addr or tlp['data'] != exp_data:
            raise AssertionError(f"Rapid trigger {vec}: mismatch")

    dut._log.info("test_rapid_triggers PASSED")


@cocotb.test()
async def test_interleaved_operations(dut):
    """Test interleaved writes, reads, and triggers."""

    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    table_req = TLPRequestSource(dut, "bar2_req_sink")
    table_cpl = TLPCompletionSink(dut, "bar2_cpl_source")
    msi_sink = MSIXTLPSink(dut)

    await reset_dut(dut)
    await table_req.reset()
    await table_cpl.reset()
    await msi_sink.reset()

    # Interleaved: program vector 0, trigger it, program vector 1, read back 0, trigger 1
    # Step 1: Program vector 0
    await table_req.write32(0x00, 0xFEE00000)
    await table_req.write32(0x04, 0x00000000)
    await table_req.write32(0x08, 0xAAAA0000)
    await table_req.write32(0x0C, 0x00000000)

    await ClockCycles(dut.sys_clk, 2)

    # Step 2: Trigger vector 0
    dut.sw_vector.value = 0
    dut.sw_valid.value = 1
    await RisingEdge(dut.sys_clk)
    dut.sw_valid.value = 0

    tlp = await msi_sink.wait_tlp(timeout_cycles=50)
    if tlp is None or tlp['data'] != 0xAAAA0000:
        raise AssertionError("First trigger failed")

    # Step 3: Program vector 1 (while vector 0 data is still valid)
    await table_req.write32(0x10, 0xFEE00100)
    await table_req.write32(0x14, 0x00000000)
    await table_req.write32(0x18, 0xBBBB1111)
    await table_req.write32(0x1C, 0x00000000)

    await ClockCycles(dut.sys_clk, 2)

    # Step 4: Read back vector 0 to verify it wasn't corrupted
    await table_req.read(0x00, length=2)
    cpl = await table_cpl.wait_completion()
    if (cpl['data'] & 0xFFFFFFFF) != 0xFEE00000:
        raise AssertionError(f"Vector 0 corrupted after programming vector 1")

    # Step 5: Trigger vector 1
    dut.sw_vector.value = 1
    dut.sw_valid.value = 1
    await RisingEdge(dut.sys_clk)
    dut.sw_valid.value = 0

    tlp = await msi_sink.wait_tlp(timeout_cycles=50)
    if tlp is None or tlp['data'] != 0xBBBB1111:
        raise AssertionError("Second trigger failed")

    # Step 6: Trigger vector 0 again to verify it still works
    dut.sw_vector.value = 0
    dut.sw_valid.value = 1
    await RisingEdge(dut.sys_clk)
    dut.sw_valid.value = 0

    tlp = await msi_sink.wait_tlp(timeout_cycles=50)
    if tlp is None or tlp['data'] != 0xAAAA0000:
        raise AssertionError("Re-trigger of vector 0 failed")

    dut._log.info("test_interleaved_operations PASSED")
