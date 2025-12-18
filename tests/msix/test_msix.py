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

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0

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
