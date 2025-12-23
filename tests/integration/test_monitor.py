#
# BSA PCIe Exerciser - Transaction Monitor Tests
#
# Copyright (c) 2025 Shareef Jalloq
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

from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder


# =============================================================================
# Register Offsets
# =============================================================================

REG_TXN_TRACE = 0x40   # Transaction FIFO read data (RO)
REG_TXN_CTRL  = 0x44   # Transaction control: [0]=enable, [1]=clear (W1C)


# =============================================================================
# FIFO Word Format (5 words per transaction)
# =============================================================================
# Word 0 (Attributes):
#   [0]     : we (1=write, 0=read)
#   [3:1]   : bar_hit[2:0]
#   [13:4]  : len[9:0] (DWORD count)
#   [17:14] : first_be[3:0]
#   [21:18] : last_be[3:0]
#   [23:22] : attr[1:0] (relaxed ordering, no snoop)
#   [25:24] : at[1:0] (address type)
#   [31:26] : reserved
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
    txn = {
        'we': (w0 >> 0) & 0x1,
        'bar_hit': (w0 >> 1) & 0x7,
        'length': (w0 >> 4) & 0x3FF,
        'first_be': (w0 >> 14) & 0xF,
        'last_be': (w0 >> 18) & 0xF,
        'attr': (w0 >> 22) & 0x3,
        'at': (w0 >> 24) & 0x3,
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

    dut._log.info(f"Captured: we={txn['we']}, bar_hit={txn['bar_hit']}, addr=0x{txn['address']:08X}")

    assert txn['we'] == 1, "Expected write transaction (we=1)"
    assert txn['bar_hit'] == 0b010, f"Expected BAR1 hit, got {txn['bar_hit']:03b}"
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

    assert txn['we'] == 1, "Expected write (we=1)"
    assert txn['address'] == test_addr, f"Address mismatch: got 0x{txn['address']:08X}"
    assert txn['length'] == 1, f"Length mismatch: got {txn['length']}"
    assert txn['first_be'] == 0xF, f"first_be mismatch: got 0x{txn['first_be']:X}"
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

    dut._log.info(f"Captured read: addr=0x{txn['address']:08X}, len={txn['length']}, we={txn['we']}")

    assert txn['we'] == 0, "Expected read (we=0)"
    assert txn['address'] == test_addr, f"Address mismatch: got 0x{txn['address']:08X}"
    assert txn['length'] == 2, f"Length mismatch: got {txn['length']}, expected 2"

    dut._log.info("test_monitor_captures_read PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_extracts_attributes(dut):
    """
    Verify monitor correctly extracts TLP attributes (attr, AT fields).

    Tests with non-zero values to ensure bits are properly captured.
    attr: [1]=Relaxed Ordering, [0]=No Snoop
    at: Address Type (0=untranslated, 1=translation request, 2=translated)
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

    dut._log.info(f"Captured: attr=0b{txn['attr']:02b}, at=0b{txn['at']:02b}")

    assert txn['attr'] == test_attr, f"Expected attr=0b{test_attr:02b}, got 0b{txn['attr']:02b}"
    assert txn['at'] == test_at, f"Expected at=0b{test_at:02b}, got 0b{txn['at']:02b}"

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

    dut._log.info(f"Test 2 Captured: attr=0b{txn2['attr']:02b}, at=0b{txn2['at']:02b}")

    assert txn2['attr'] == test_attr2, f"Expected attr=0b{test_attr2:02b}, got 0b{txn2['attr']:02b}"
    assert txn2['at'] == test_at2, f"Expected at=0b{test_at2:02b}, got 0b{txn2['at']:02b}"

    dut._log.info("test_monitor_extracts_attributes PASSED")


@cocotb.test(timeout_time=100, timeout_unit="us")
async def test_monitor_captures_bar_hits(dut):
    """
    Verify monitor correctly captures different BAR hit values.

    Tests BAR0, BAR1, and BAR2 hits to ensure bar_hit field is captured correctly.
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

        expected_bar_hit = tc['bar_hit'] & 0x7  # Only lower 3 bits stored
        dut._log.info(f"BAR{tc['bar_num']}: captured bar_hit=0b{txn['bar_hit']:03b}, "
                      f"addr=0x{txn['address']:08X}, data=0x{txn['data_lo']:08X}")

        assert txn['bar_hit'] == expected_bar_hit, \
            f"BAR{tc['bar_num']}: Expected bar_hit=0b{expected_bar_hit:03b}, got 0b{txn['bar_hit']:03b}"
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
