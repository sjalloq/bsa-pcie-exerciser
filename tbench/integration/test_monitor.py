#
# BSA PCIe Exerciser - Transaction Monitor Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Integration tests for the Transaction Monitor.

The Transaction Monitor captures inbound TLPs and makes their metadata
available via CSR registers. This is critical for BSA compliance testing
as the exerciser must be able to observe host-initiated transactions.
"""

import sys
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tbench.common.pcie_bfm import PCIeBFM
from tbench.common.tlp_builder import TLPBuilder


# =============================================================================
# Register Offsets
# =============================================================================

REG_TXN_TRACE = 0x40   # Transaction FIFO read data (RO)
REG_TXN_CTRL  = 0x44   # Transaction control:
                       #   [0] = enable (R/W)
                       #   [1] = clear (W1C, always reads 0)
                       #   [2] = overflow (RO, sticky until clear)
                       #   [15:8] = count (RO, transactions in FIFO)


# =============================================================================
# FIFO Word Format (5 words per transaction, ACS TXN_TRACE encoding)
# =============================================================================
# Word 0 (TX_ATTRIBUTES):
#   [0]     : type (CFG only: 0=Type0, 1=Type1)
#   [1]     : R/W (1=read, 0=write)
#   [2]     : CFG/MEM (1=CFG, 0=MEM)
#   [15:3]  : reserved
#   [31:16] : byte size one-hot (bit = log2(bytes))
#
# Word 1: ADDRESS[31:0]
# Word 2: ADDRESS[63:32]
# Word 3: DATA[31:0]
# Word 4: DATA[63:32]


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    await ClockCycles(dut.sys_clk, 10)
    dut.sys_rst.value = 0
    await ClockCycles(dut.sys_clk, 10)


async def write_bar0_register(bfm, offset, data):
    """Write a 32-bit value to a BAR0 register."""
    data_bytes = data.to_bytes(4, 'little')
    beats = TLPBuilder.memory_write_32(
        address=offset,
        data_bytes=data_bytes,
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)
    await ClockCycles(bfm.clk, 5)


async def read_bar0_register(bfm, offset, tag=0):
    """Read a 32-bit value from a BAR0 register."""
    beats = TLPBuilder.memory_read_32(
        address=offset,
        length_dw=1,
        requester_id=0x0100,
        tag=tag,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)

    cpl = await bfm.capture_tlp(timeout_cycles=200)
    if cpl is None:
        return None

    # Extract data from completion (big-endian wire format)
    raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF
    return int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')


async def read_txn_ctrl_status(bfm, tag=0):
    """
    Read TXN_CTRL and extract status fields.

    Returns:
        Dict with 'enable', 'overflow', 'count' fields, or None on error.
    """
    value = await read_bar0_register(bfm, REG_TXN_CTRL, tag=tag)
    if value is None:
        return None
    return {
        'enable': (value >> 0) & 0x1,
        'overflow': (value >> 2) & 0x1,
        'count': (value >> 8) & 0xFF,
    }


async def read_fifo_transaction(bfm):
    """
    Read a complete transaction from the monitor FIFO (5 words).

    Returns:
        Dict with parsed transaction fields, or None if FIFO empty.
    """
    words = []
    for i in range(5):
        word = await read_bar0_register(bfm, REG_TXN_TRACE, tag=0x20 + i)
        if word is None:
            return None
        # 0xFFFFFFFF indicates empty FIFO
        if i == 0 and word == 0xFFFFFFFF:
            return None
        words.append(word)

    # Parse word 0 (attributes)
    w0 = words[0]
    size_onehot = (w0 >> 16) & 0xFFFF

    size_bytes = 0
    if size_onehot == (1 << 0):
        size_bytes = 1
    elif size_onehot == (1 << 1):
        size_bytes = 2
    elif size_onehot == (1 << 2):
        size_bytes = 4
    elif size_onehot == (1 << 3):
        size_bytes = 8

    is_read = (w0 >> 1) & 0x1
    is_cfg = (w0 >> 2) & 0x1
    txn = {
        'type': (w0 >> 0) & 0x1,
        'is_read': is_read,
        'is_cfg': is_cfg,
        'size_onehot': size_onehot,
        'size_bytes': size_bytes,
        'we': 0 if is_read else 1,
        'addr_lo': words[1],
        'addr_hi': words[2],
        'data_lo': words[3],
        'data_hi': words[4],
    }
    txn['address'] = (txn['addr_hi'] << 32) | txn['addr_lo']
    txn['data'] = (txn['data_hi'] << 32) | txn['data_lo']

    return txn


# =============================================================================
# Transaction Monitor Tests
# =============================================================================

@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_enable_capture(dut):
    """
    Test enabling transaction capture via TXN_CTRL.

    Verifies that transactions are only captured when TXN_CTRL[0]=1.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    dut._log.info("Testing monitor enable/disable")

    # Enable capture first, then clear FIFO to remove the enable transaction itself
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)  # Enable capture
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear FIFO (keep enabled)
    await ClockCycles(bfm.clk, 5)

    # Inject a test write to BAR1 (this should be captured)
    test_write = TLPBuilder.memory_write_32(
        address=0x100,
        data_bytes=b'\x11\x22\x33\x44',
        requester_id=0x0100,
        tag=0x10,
    )
    await bfm.inject_tlp(test_write, bar_hit=0b000010)  # BAR1
    await ClockCycles(bfm.clk, 20)

    # Read back captured transaction
    txn = await read_fifo_transaction(bfm)

    if txn is None:
        raise AssertionError("No transaction captured when monitor enabled")

    dut._log.info(
        f"Captured: read={txn['is_read']}, cfg={txn['is_cfg']}, "
        f"size={txn['size_bytes']}B, addr=0x{txn['address']:08X}"
    )

    assert txn['we'] == 1, "Expected write transaction"
    assert txn['is_cfg'] == 0, "Expected memory transaction"
    assert txn['size_bytes'] == 4, f"Expected 4-byte write, got {txn['size_bytes']}B"
    assert txn['address'] == 0x100, f"Expected address 0x100, got 0x{txn['address']:08X}"
    assert txn['data_lo'] == 0x44332211, f"Expected data 0x44332211, got 0x{txn['data_lo']:08X}"

    dut._log.info("test_monitor_enable_capture PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_write(dut):
    """
    Verify monitor captures inbound Memory Write TLPs correctly.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Enable capture first, then clear FIFO to remove the enable transaction itself
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)  # Enable capture
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear FIFO (keep enabled)
    await ClockCycles(bfm.clk, 5)

    # Inject a write with specific data
    test_addr = 0x200
    test_data = 0xDEADBEEF
    test_write = TLPBuilder.memory_write_32(
        address=test_addr,
        data_bytes=test_data.to_bytes(4, 'little'),
        requester_id=0x0100,
        tag=0x11,
    )
    await bfm.inject_tlp(test_write, bar_hit=0b000010)  # BAR1
    await ClockCycles(bfm.clk, 20)

    # Read captured transaction
    txn = await read_fifo_transaction(bfm)

    if txn is None:
        raise AssertionError("No transaction captured")

    dut._log.info(f"Captured write: addr=0x{txn['address']:08X}, data=0x{txn['data_lo']:08X}")

    assert txn['we'] == 1, "Expected write"
    assert txn['is_cfg'] == 0, "Expected memory transaction"
    assert txn['size_bytes'] == 4, f"Expected 4-byte write, got {txn['size_bytes']}B"
    assert txn['address'] == test_addr, f"Address mismatch: got 0x{txn['address']:08X}"
    assert txn['data_lo'] == test_data, f"Data mismatch: got 0x{txn['data_lo']:08X}, expected 0x{test_data:08X}"

    dut._log.info("test_monitor_captures_write PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_read(dut):
    """
    Verify monitor captures inbound Memory Read TLPs correctly.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Enable capture first, then clear FIFO to remove the enable transaction itself
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)  # Enable capture
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear FIFO (keep enabled)
    await ClockCycles(bfm.clk, 5)

    # Inject a read request
    test_addr = 0x300
    test_read = TLPBuilder.memory_read_32(
        address=test_addr,
        length_dw=2,  # 2 DWORDs
        requester_id=0x0100,
        tag=0x12,
    )
    await bfm.inject_tlp(test_read, bar_hit=0b000010)  # BAR1

    # Wait for capture and consume the completion
    await ClockCycles(bfm.clk, 20)
    await bfm.capture_tlp(timeout_cycles=200)  # Consume completion

    # Read captured transaction
    txn = await read_fifo_transaction(bfm)

    if txn is None:
        raise AssertionError("No transaction captured")

    dut._log.info(
        f"Captured read: addr=0x{txn['address']:08X}, size={txn['size_bytes']}B, read={txn['is_read']}"
    )

    assert txn['we'] == 0, "Expected read"
    assert txn['is_cfg'] == 0, "Expected memory transaction"
    assert txn['address'] == test_addr, f"Address mismatch: got 0x{txn['address']:08X}"
    assert txn['size_bytes'] == 8, f"Expected 8-byte read, got {txn['size_bytes']}B"

    dut._log.info("test_monitor_captures_read PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_extracts_attributes(dut):
    """
    Verify monitor captures transactions even when attr/AT are non-zero.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Enable the monitor (and clear the FIFO)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # {CLEAR, ENABLE}
    await ClockCycles(bfm.clk, 5)

    # Test 1: Inject write with No Snoop (attr=0b01) and Translated address (at=0b10)
    test_attr = 0b01  # No Snoop
    test_at = 0b10    # Translated
    test_write = TLPBuilder.memory_write_32(
        address=0x400,
        data_bytes=b'\xAB\xCD\xEF\x01',
        requester_id=0x0100,
        tag=0x13,
        attr=test_attr,
        at=test_at,
    )
    await bfm.inject_tlp(test_write, bar_hit=0b000010)
    await ClockCycles(bfm.clk, 20)

    txn = await read_fifo_transaction(bfm)

    if txn is None:
        raise AssertionError("No transaction captured")

    dut._log.info(
        f"Captured: read={txn['is_read']}, cfg={txn['is_cfg']}, size={txn['size_bytes']}B"
    )
    assert txn['we'] == 1, "Expected write"
    assert txn['is_cfg'] == 0, "Expected memory transaction"
    assert txn['size_bytes'] == 4, f"Expected 4-byte write, got {txn['size_bytes']}B"

    # Test 2: Clear and test with Relaxed Ordering (attr=0b10) and Translation Request (at=0b01)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear FIFO (keep enabled)
    await ClockCycles(bfm.clk, 5)

    test_attr2 = 0b10  # Relaxed Ordering
    test_at2 = 0b01    # Translation Request
    test_write2 = TLPBuilder.memory_write_32(
        address=0x500,
        data_bytes=b'\x12\x34\x56\x78',
        requester_id=0x0100,
        tag=0x14,
        attr=test_attr2,
        at=test_at2,
    )
    await bfm.inject_tlp(test_write2, bar_hit=0b000010)
    await ClockCycles(bfm.clk, 20)

    txn2 = await read_fifo_transaction(bfm)

    if txn2 is None:
        raise AssertionError("No transaction captured for test 2")

    dut._log.info(
        f"Test 2 Captured: read={txn2['is_read']}, cfg={txn2['is_cfg']}, size={txn2['size_bytes']}B"
    )
    assert txn2['we'] == 1, "Expected write"
    assert txn2['is_cfg'] == 0, "Expected memory transaction"
    assert txn2['size_bytes'] == 4, f"Expected 4-byte write, got {txn2['size_bytes']}B"

    dut._log.info("test_monitor_extracts_attributes PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_bar_hits(dut):
    """
    Verify monitor captures writes to different BAR windows.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Test data for each BAR
    test_cases = [
        {'bar_hit': 0b000001, 'bar_num': 0, 'address': 0x100, 'data': 0x11111111},
        {'bar_hit': 0b000010, 'bar_num': 1, 'address': 0x200, 'data': 0x22222222},
        {'bar_hit': 0b000100, 'bar_num': 2, 'address': 0x300, 'data': 0x33333333},
    ]

    for tc in test_cases:
        # Enable capture and clear FIFO
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
        await ClockCycles(bfm.clk, 5)
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
        await ClockCycles(bfm.clk, 5)

        # Inject write to specific BAR
        test_write = TLPBuilder.memory_write_32(
            address=tc['address'],
            data_bytes=tc['data'].to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=0x30 + tc['bar_num'],
        )
        await bfm.inject_tlp(test_write, bar_hit=tc['bar_hit'])
        await ClockCycles(bfm.clk, 20)

        txn = await read_fifo_transaction(bfm)

        if txn is None:
            raise AssertionError(f"No transaction captured for BAR{tc['bar_num']}")

        dut._log.info(
            f"BAR{tc['bar_num']}: captured addr=0x{txn['address']:08X}, data=0x{txn['data_lo']:08X}"
        )

        assert txn['address'] == tc['address'], \
            f"BAR{tc['bar_num']}: Expected address=0x{tc['address']:08X}, got 0x{txn['address']:08X}"
        assert txn['data_lo'] == tc['data'], \
            f"BAR{tc['bar_num']}: Expected data=0x{tc['data']:08X}, got 0x{txn['data_lo']:08X}"

    dut._log.info("test_monitor_captures_bar_hits PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_fifo_clear(dut):
    """
    Verify FIFO can be cleared via TXN_CTRL[1].
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Enable capture first, then clear FIFO to remove the enable transaction itself
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)  # Enable capture
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear FIFO (keep enabled)
    await ClockCycles(bfm.clk, 5)

    # Inject a few transactions with distinct data
    test_data = [0xAABBCCDD, 0x11223344, 0x55667788]
    for i in range(3):
        test_write = TLPBuilder.memory_write_32(
            address=0x500 + i * 8,
            data_bytes=test_data[i].to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=0x20 + i,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 10)

    # Verify we have transactions and first one has correct data
    txn = await read_fifo_transaction(bfm)
    assert txn is not None, "Expected transaction before clear"
    assert txn['address'] == 0x500, f"Expected first txn addr=0x500, got 0x{txn['address']:08X}"
    assert txn['data_lo'] == test_data[0], f"Expected first txn data=0x{test_data[0]:08X}, got 0x{txn['data_lo']:08X}"
    dut._log.info(f"Verified FIFO has transactions (first: addr=0x{txn['address']:08X}, data=0x{txn['data_lo']:08X})")

    # Clear FIFO (keep enabled so subsequent clear check works)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear + keep enabled
    await ClockCycles(bfm.clk, 10)

    # Verify FIFO is empty (no re-enable needed, it stayed enabled)
    txn = await read_fifo_transaction(bfm)

    if txn is not None:
        raise AssertionError("FIFO should be empty after clear")

    dut._log.info("FIFO cleared successfully")
    dut._log.info("test_monitor_fifo_clear PASSED")


# =============================================================================
# Monitor Byte Enable Capture Tests
# =============================================================================

@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_first_be_partial(dut):
    """
    Verify monitor encodes size from partial first_be values.

    Tests various first_be patterns to validate byte-size encoding.
    This is required for BSA ACS e022 compliance verification.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Test cases: (first_be, expected_bytes, description)
    test_cases = [
        (0x1, 1, "byte 0 only"),
        (0x3, 2, "bytes 0-1"),
        (0x5, 2, "bytes 0,2 (non-contiguous)"),
        (0xC, 2, "bytes 2-3"),
        (0xF, 4, "all bytes"),
    ]

    for first_be, expected_bytes, desc in test_cases:
        dut._log.info(f"--- Testing first_be=0x{first_be:X} ({desc}) ---")

        # Enable and clear FIFO
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
        await ClockCycles(bfm.clk, 5)
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
        await ClockCycles(bfm.clk, 5)

        # Inject write with specific first_be
        test_write = TLPBuilder.memory_write_32(
            address=0x100,
            data_bytes=b'\xAA\xBB\xCC\xDD',
            requester_id=0x0100,
            tag=0x40,
            first_be=first_be,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)  # BAR1
        await ClockCycles(bfm.clk, 20)

        # Read captured transaction
        txn = await read_fifo_transaction(bfm)

        if txn is None:
            raise AssertionError(f"No transaction captured for first_be=0x{first_be:X}")

        dut._log.info(f"Captured: size={txn['size_bytes']}B")
        assert txn['size_bytes'] == expected_bytes, \
            f"Size mismatch: expected {expected_bytes}B, got {txn['size_bytes']}B"

    dut._log.info("test_monitor_captures_first_be_partial PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_multi_dword_be(dut):
    """
    Verify monitor encodes size correctly for multi-DWORD writes.

    For multi-DWORD TLPs, size should reflect enabled bytes in the beat.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Test cases: (first_be, last_be, expected_bytes, description)
    test_cases = [
        (0xF, 0xF, 8, "full 2 DWORDs"),
        (0x3, 0xC, 4, "lower first, upper last"),
        (0x8, 0x1, 2, "byte 3 first, byte 0 last"),
    ]

    for first_be, last_be, expected_bytes, desc in test_cases:
        dut._log.info(f"--- Testing {desc}: first_be=0x{first_be:X}, last_be=0x{last_be:X} ---")

        # Enable and clear FIFO
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
        await ClockCycles(bfm.clk, 5)
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
        await ClockCycles(bfm.clk, 5)

        # Inject multi-DWORD write
        data_bytes = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x77, 0x88])
        test_write = TLPBuilder.memory_write_32(
            address=0x200,
            data_bytes=data_bytes,
            requester_id=0x0100,
            tag=0x50,
            first_be=first_be,
            last_be=last_be,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)  # BAR1
        await ClockCycles(bfm.clk, 20)

        # Read captured transaction
        txn = await read_fifo_transaction(bfm)

        if txn is None:
            raise AssertionError(f"No transaction captured for {desc}")

        dut._log.info(f"Captured: size={txn['size_bytes']}B")
        assert txn['size_bytes'] == expected_bytes, \
            f"Size mismatch: expected {expected_bytes}B, got {txn['size_bytes']}B"

        dut._log.info(f"PASS: {desc}")

    dut._log.info("test_monitor_captures_multi_dword_be PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_read_first_be(dut):
    """
    Verify monitor encodes size correctly for Memory Read TLPs.

    Memory reads use first_be to indicate which bytes are requested.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Test cases for reads: (first_be, expected_bytes, description)
    test_cases = [
        (0xF, 4, "full DWORD"),
        (0x3, 2, "lower 2 bytes"),
        (0xC, 2, "upper 2 bytes"),
        (0x1, 1, "byte 0 only"),
    ]

    for first_be, expected_bytes, desc in test_cases:
        dut._log.info(f"--- Testing read first_be=0x{first_be:X} ({desc}) ---")

        # Enable and clear FIFO
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
        await ClockCycles(bfm.clk, 5)
        await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
        await ClockCycles(bfm.clk, 5)

        # Inject read with specific first_be
        test_read = TLPBuilder.memory_read_32(
            address=0x300,
            length_dw=1,
            requester_id=0x0100,
            tag=0x60,
            first_be=first_be,
        )
        await bfm.inject_tlp(test_read, bar_hit=0b000010)  # BAR1

        # Wait for processing and consume completion
        await ClockCycles(bfm.clk, 20)
        await bfm.capture_tlp(timeout_cycles=200)

        # Read captured transaction
        txn = await read_fifo_transaction(bfm)

        if txn is None:
            raise AssertionError(f"No transaction captured for read first_be=0x{first_be:X}")

        dut._log.info(f"Captured: we={txn['we']}, size={txn['size_bytes']}B")

        assert txn['we'] == 0, "Expected read (we=0)"
        assert txn['size_bytes'] == expected_bytes, \
            f"Size mismatch: expected {expected_bytes}B, got {txn['size_bytes']}B"

        dut._log.info(f"PASS: read first_be=0x{first_be:X}")

    dut._log.info("test_monitor_captures_read_first_be PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_be_zero_handling(dut):
    """
    Verify monitor handles first_be=0 (no bytes enabled).

    Per PCIe spec, first_be=0 is valid for zero-length reads.
    This tests edge case handling.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Enable and clear FIFO
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
    await ClockCycles(bfm.clk, 5)

    # Inject write with first_be=0 (unusual but valid)
    test_write = TLPBuilder.memory_write_32(
        address=0x400,
        data_bytes=b'\x00\x00\x00\x00',
        requester_id=0x0100,
        tag=0x70,
        first_be=0x0,
    )
    await bfm.inject_tlp(test_write, bar_hit=0b000010)  # BAR1
    await ClockCycles(bfm.clk, 20)

    # Read captured transaction
    txn = await read_fifo_transaction(bfm)

    if txn is None:
        raise AssertionError("No transaction captured for first_be=0x0")

    dut._log.info(f"Captured: size={txn['size_bytes']}B")

    assert txn['size_bytes'] == 0, \
        f"Size mismatch: expected 0B, got {txn['size_bytes']}B"

    dut._log.info("test_monitor_be_zero_handling PASSED")


# =============================================================================
# Monitor FIFO Overflow Tests
# =============================================================================

# FIFO depth is 32 transactions (BSA spec maximum) per bsa_pcie_exerciser.py
# Note: BAR0 register accesses (TXN_CTRL reads/writes) are also captured!

@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_monitor_fifo_overflow_stops_capture(dut):
    """
    Verify FIFO stops accepting transactions when full and sets overflow flag.

    When the FIFO reaches capacity, subsequent transactions should be dropped
    and the overflow flag should be set. The count field should accurately
    reflect the number of transactions buffered.

    Note: The monitor captures ALL transactions including BAR0 register accesses,
    so we account for this in our calculations.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    FIFO_DEPTH = 32  # Actual depth from bsa_pcie_exerciser.py
    OVERFLOW_COUNT = 5

    dut._log.info(f"Testing FIFO overflow: injecting {FIFO_DEPTH + OVERFLOW_COUNT} transactions to BAR1")

    # Enable capture and clear
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
    await ClockCycles(bfm.clk, 5)

    # Inject transactions with distinct data patterns (to BAR1)
    # These are pure writes, no reads needed, so only these get captured
    for i in range(FIFO_DEPTH + OVERFLOW_COUNT):
        data_pattern = 0x10000000 | (i << 16) | i
        test_write = TLPBuilder.memory_write_32(
            address=0x100 + (i * 4),
            data_bytes=data_pattern.to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=i,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)  # BAR1
        await ClockCycles(bfm.clk, 5)

    dut._log.info("All transactions injected, checking status")

    # Check status after overflow (this read will be captured too)
    status = await read_txn_ctrl_status(bfm, tag=0xA1)
    assert status is not None, "Failed to read TXN_CTRL after overflow"
    dut._log.info(f"After overflow: enable={status['enable']}, overflow={status['overflow']}, count={status['count']}")

    # Verify overflow flag is set (we injected FIFO_DEPTH + OVERFLOW_COUNT = 37 txns)
    assert status['overflow'] == 1, "Overflow flag should be set after exceeding capacity"

    # Verify count is at maximum (FIFO_DEPTH)
    assert status['count'] == FIFO_DEPTH, \
        f"Count should be {FIFO_DEPTH}, got {status['count']}"

    # Read all captured transactions
    captured_count = 0
    first_txn = None

    for i in range(FIFO_DEPTH + 10):  # Try to read more than we expect
        txn = await read_fifo_transaction(bfm)
        if txn is None:
            break
        captured_count += 1
        if first_txn is None:
            first_txn = txn
        if i < 5:  # Log first few
            dut._log.info(f"Transaction {i}: addr=0x{txn['address']:08X}, data=0x{txn['data_lo']:08X}")

    dut._log.info(f"Captured {captured_count} transactions from FIFO")

    # Verify we captured exactly FIFO_DEPTH transactions
    assert captured_count == FIFO_DEPTH, \
        f"Expected {FIFO_DEPTH} captured transactions, got {captured_count}"

    # Verify first transaction has expected data (wasn't overwritten)
    if first_txn is not None:
        expected_addr = 0x100
        expected_data = 0x10000000
        assert first_txn['address'] == expected_addr, \
            f"First transaction address corrupted: got 0x{first_txn['address']:08X}"
        assert first_txn['data_lo'] == expected_data, \
            f"First transaction data corrupted: got 0x{first_txn['data_lo']:08X}"

    # Verify overflow flag is still set (sticky) - this read also captured
    status = await read_txn_ctrl_status(bfm, tag=0xA2)
    assert status['overflow'] == 1, "Overflow flag should remain set (sticky)"

    dut._log.info("PASS: FIFO correctly stopped at capacity, overflow flag set")
    dut._log.info("test_monitor_fifo_overflow_stops_capture PASSED")


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_monitor_fifo_overflow_recovery(dut):
    """
    Verify FIFO and overflow flag are cleared together, allowing recovery.

    After overflow, the clear bit should:
    1. Empty the FIFO
    2. Clear the sticky overflow flag
    3. Allow new transactions to be captured
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    FIFO_DEPTH = 32  # Actual depth from bsa_pcie_exerciser.py

    dut._log.info("Filling FIFO to capacity and causing overflow")

    # Enable capture and clear
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
    await ClockCycles(bfm.clk, 5)

    # Fill FIFO completely + overflow
    for i in range(FIFO_DEPTH + 5):
        test_write = TLPBuilder.memory_write_32(
            address=0x200 + (i * 4),
            data_bytes=b'\xAA\xBB\xCC\xDD',
            requester_id=0x0100,
            tag=i & 0xFF,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 3)

    # Disable capture before reading status (so reads aren't captured)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x00)
    await ClockCycles(bfm.clk, 5)

    # Verify overflow is set
    status = await read_txn_ctrl_status(bfm, tag=0xB0)
    dut._log.info(f"Before clear: overflow={status['overflow']}, count={status['count']}")
    assert status['overflow'] == 1, "Overflow should be set after exceeding capacity"
    assert status['count'] == FIFO_DEPTH, f"Count should be {FIFO_DEPTH}"

    dut._log.info("Clearing FIFO and overflow flag")

    # Clear FIFO (this should also clear overflow), keep disabled
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x02)  # Clear only, stay disabled
    await ClockCycles(bfm.clk, 10)

    # Verify FIFO is empty and overflow is cleared
    status = await read_txn_ctrl_status(bfm, tag=0xB1)
    dut._log.info(f"After clear: overflow={status['overflow']}, count={status['count']}")
    assert status['overflow'] == 0, "Overflow should be cleared after clear"
    assert status['count'] == 0, "Count should be 0 after clear"

    # Re-enable capture for new transactions
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)  # Enable
    await ClockCycles(bfm.clk, 5)

    dut._log.info("Injecting new transactions after recovery")

    # Inject new transactions
    for i in range(5):
        data_pattern = 0x55000000 | i
        test_write = TLPBuilder.memory_write_32(
            address=0x300 + (i * 4),
            data_bytes=data_pattern.to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=0x80 + i,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 5)

    # Disable capture before verifying
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x00)
    await ClockCycles(bfm.clk, 5)

    # Verify count reflects new transactions
    # TXN_CTRL writes are excluded from capture, so only BAR1 writes are counted
    status = await read_txn_ctrl_status(bfm, tag=0xB2)
    dut._log.info(f"After new txns: overflow={status['overflow']}, count={status['count']}")
    assert status['count'] == 5, f"Count should be 5 (BAR1 writes only), got {status['count']}"
    assert status['overflow'] == 0, "Overflow should still be 0"

    # Verify all 5 BAR1 transactions were captured
    for i in range(5):
        txn = await read_fifo_transaction(bfm)
        assert txn is not None, f"Expected transaction {i}"
        expected_addr = 0x300 + (i * 4)
        expected_data = 0x55000000 | i
        assert txn['address'] == expected_addr, \
            f"Expected address 0x{expected_addr:08X}, got 0x{txn['address']:08X}"
        assert txn['data_lo'] == expected_data, \
            f"Expected data 0x{expected_data:08X}, got 0x{txn['data_lo']:08X}"
    dut._log.info("All 5 BAR1 transactions captured correctly")

    dut._log.info("PASS: FIFO and overflow recovered after clear")
    dut._log.info("test_monitor_fifo_overflow_recovery PASSED")


@cocotb.test(timeout_time=500, timeout_unit="us")
async def test_monitor_fifo_overflow_lockout(dut):
    """
    Verify that overflow lockout prevents new captures until clear.

    Once overflow is set, no new transactions should be captured even if
    there is space in the FIFO (after reads). The lockout persists until
    the clear bit is written.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    FIFO_DEPTH = 32  # Actual depth from bsa_pcie_exerciser.py

    dut._log.info("Testing overflow lockout behavior")

    # Enable capture and clear
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)
    await ClockCycles(bfm.clk, 5)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)
    await ClockCycles(bfm.clk, 5)

    # Fill FIFO and cause overflow
    dut._log.info(f"Filling FIFO with {FIFO_DEPTH + 2} transactions (overflow by 2)")
    for i in range(FIFO_DEPTH + 2):
        data_pattern = 0x11110000 | i
        test_write = TLPBuilder.memory_write_32(
            address=0x400 + (i * 4),
            data_bytes=data_pattern.to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=i & 0xFF,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 3)

    # Verify overflow is set (overflow lockout prevents new captures even with enable=1)
    status = await read_txn_ctrl_status(bfm, tag=0xC0)
    dut._log.info(f"After overflow: overflow={status['overflow']}, count={status['count']}")
    assert status['overflow'] == 1, "Overflow should be set"
    assert status['count'] == FIFO_DEPTH, f"Count should be {FIFO_DEPTH}"

    # Read out half the transactions (creates space, but lockout should prevent new captures)
    dut._log.info("Reading 16 transactions to create space")
    for i in range(16):
        txn = await read_fifo_transaction(bfm)
        assert txn is not None, f"Expected transaction {i}"

    # Verify count decreased but overflow still set
    # Note: reads while in overflow lockout shouldn't be captured
    status = await read_txn_ctrl_status(bfm, tag=0xC1)
    dut._log.info(f"After partial read: overflow={status['overflow']}, count={status['count']}")
    assert status['overflow'] == 1, "Overflow should still be set (sticky)"
    assert status['count'] == 16, f"Count should be 16 after reading 16, got {status['count']}"

    # Try to inject more transactions (should be dropped due to lockout)
    dut._log.info("Injecting transactions while locked out (should be dropped)")
    for i in range(4):
        data_pattern = 0x22220000 | i
        test_write = TLPBuilder.memory_write_32(
            address=0x500 + (i * 4),
            data_bytes=data_pattern.to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=0x80 + i,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 5)

    # Verify count is unchanged (lockout prevented captures)
    status = await read_txn_ctrl_status(bfm, tag=0xC2)
    dut._log.info(f"After lockout inject: overflow={status['overflow']}, count={status['count']}")
    assert status['count'] == 16, f"Count should still be 16 (lockout), got {status['count']}"

    # Clear and disable before checking state
    dut._log.info("Clearing overflow and verifying normal operation")
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x02)  # Clear only, stay disabled
    await ClockCycles(bfm.clk, 10)

    status = await read_txn_ctrl_status(bfm, tag=0xC3)
    assert status['overflow'] == 0, "Overflow should be cleared"
    assert status['count'] == 0, "Count should be 0 after clear"
    dut._log.info(f"After clear: overflow={status['overflow']}, count={status['count']}")

    # Re-enable for new captures
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x01)  # Enable
    await ClockCycles(bfm.clk, 5)

    # Inject new transactions (should be captured now)
    dut._log.info("Injecting transactions after clear (should be captured)")
    for i in range(4):
        data_pattern = 0x33330000 | i
        test_write = TLPBuilder.memory_write_32(
            address=0x600 + (i * 4),
            data_bytes=data_pattern.to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=0x90 + i,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 5)

    # Disable before checking (TXN_CTRL writes are NOT captured)
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x00)
    await ClockCycles(bfm.clk, 5)

    status = await read_txn_ctrl_status(bfm, tag=0xC4)
    dut._log.info(f"After post-clear inject: overflow={status['overflow']}, count={status['count']}")
    # TXN_CTRL writes are excluded from capture, so only BAR1 writes are counted
    assert status['count'] == 4, f"Count should be 4 (BAR1 writes only), got {status['count']}"

    dut._log.info("PASS: Overflow lockout correctly prevented captures until clear")
    dut._log.info("test_monitor_fifo_overflow_lockout PASSED")


@cocotb.test(timeout_time=200, timeout_unit="us")
async def test_monitor_count_tracking(dut):
    """
    Verify count field accurately tracks transactions as they're added and read.

    Key insight: Disable capture before reading TXN_CTRL/TXN_TRACE so those
    reads don't pollute the count.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    dut._log.info("Testing count field tracking")

    # Enable capture and clear
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x03)  # Clear + enable
    await ClockCycles(bfm.clk, 5)

    # Inject 5 transactions
    dut._log.info("Injecting 5 transactions to BAR1")
    for i in range(5):
        test_write = TLPBuilder.memory_write_32(
            address=0x100 + (i * 4),
            data_bytes=(0x11110000 | i).to_bytes(4, 'little'),
            requester_id=0x0100,
            tag=i,
        )
        await bfm.inject_tlp(test_write, bar_hit=0b000010)
        await ClockCycles(bfm.clk, 5)

    # Disable capture before checking count
    # Note: TXN_CTRL writes go directly to registers, not through PCIe depacketizer,
    # so they are NOT captured by the monitor
    await write_bar0_register(bfm, REG_TXN_CTRL, 0x00)  # Disable
    await ClockCycles(bfm.clk, 5)

    # Now check count - should be exactly 5 (only BAR1 writes captured)
    status = await read_txn_ctrl_status(bfm, tag=0xD0)
    dut._log.info(f"After injecting 5 writes: count={status['count']}")
    assert status['count'] == 5, f"Count should be 5, got {status['count']}"

    # Read 3 transactions from FIFO (capture still disabled)
    dut._log.info("Reading 3 transactions from FIFO")
    for i in range(3):
        txn = await read_fifo_transaction(bfm)
        assert txn is not None, f"Expected transaction {i}"

    # Check count decreased to 2 (was 5, read 3)
    status = await read_txn_ctrl_status(bfm, tag=0xE0)
    dut._log.info(f"After reading 3 transactions: count={status['count']}")
    assert status['count'] == 2, f"Count should be 2, got {status['count']}"

    # Read remaining 2 transactions
    for i in range(2):
        txn = await read_fifo_transaction(bfm)
        assert txn is not None, f"Expected transaction {i}"

    # Verify count is 0
    status = await read_txn_ctrl_status(bfm, tag=0xE1)
    dut._log.info(f"After reading all: count={status['count']}")
    assert status['count'] == 0, f"Count should be 0, got {status['count']}"

    dut._log.info("PASS: Count field correctly tracks transaction flow")
    dut._log.info("test_monitor_count_tracking PASSED")
