#
# BSA PCIe Exerciser - Byte Enable Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Tests for partial byte enable handling (BSA ACS e022 compliance).
#

"""
Byte Enable Tests for BSA PCIe Exerciser.

These tests verify correct handling of partial byte enables in memory writes,
which is required for BSA ACS test e022 compliance.

PCIe byte enables:
- first_be[3:0]: Byte enables for first DWORD
- last_be[3:0]: Byte enables for last DWORD (multi-DWORD only)

Byte enable encoding (for each nibble):
- 0xF = all 4 bytes enabled
- 0x3 = lower 2 bytes (bytes 0-1)
- 0xC = upper 2 bytes (bytes 2-3)
- 0x1, 0x2, 0x4, 0x8 = single byte
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder


async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    await ClockCycles(dut.sys_clk, 10)
    dut.sys_rst.value = 0
    await ClockCycles(dut.sys_clk, 10)


async def read_dword(bfm, bar_hit, offset, tag=0):
    """Read a DWORD from specified BAR and return the value."""
    beats = TLPBuilder.memory_read_32(offset, length_dw=1, tag=tag)
    await bfm.inject_tlp(beats, bar_hit=bar_hit)
    cpl = await bfm.capture_tlp(timeout_cycles=200)
    if cpl and len(cpl) >= 2:
        raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF
        return int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')
    return None


async def write_dword(bfm, bar_hit, offset, value, tag=0, first_be=0xF):
    """Write a DWORD to specified BAR with optional byte enables."""
    data_bytes = value.to_bytes(4, 'little')
    beats = TLPBuilder.memory_write_32(offset, data_bytes, tag=tag, first_be=first_be)
    await bfm.inject_tlp(beats, bar_hit=bar_hit)
    await ClockCycles(bfm.clk, 5)


# =============================================================================
# BAR1 Buffer Byte Enable Tests
# =============================================================================
# BAR1 is a simple memory buffer, ideal for testing byte enables without
# register-specific behavior interfering.

@cocotb.test()
async def test_bar1_partial_write_lower_bytes(dut):
    """
    Test partial write to lower 2 bytes of a DWORD (first_be=0x3).

    This verifies that only bytes 0-1 are modified while bytes 2-3 are preserved.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    BAR1_HIT = 0b000010
    OFFSET = 0x00

    # Step 1: Initialize with known pattern
    initial_value = 0xDEADBEEF
    await write_dword(bfm, BAR1_HIT, OFFSET, initial_value, tag=1)

    # Verify initial write
    readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=2)
    assert readback == initial_value, f"Initial write failed: got 0x{readback:08X}"

    # Step 2: Partial write to lower 2 bytes only (first_be=0x3)
    # Write 0x00001234, but only bytes 0-1 should be written
    partial_value = 0x00005678
    await write_dword(bfm, BAR1_HIT, OFFSET, partial_value, tag=3, first_be=0x3)

    # Step 3: Read back and verify
    # Expected: 0xDEAD5678 (upper bytes preserved, lower bytes updated)
    readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=4)
    expected = 0xDEAD5678

    if readback == expected:
        dut._log.info(f"PASS: Partial write (first_be=0x3) correct: 0x{readback:08X}")
    else:
        raise AssertionError(
            f"Partial write failed!\n"
            f"  Initial:  0x{initial_value:08X}\n"
            f"  Written:  0x{partial_value:08X} (first_be=0x3)\n"
            f"  Expected: 0x{expected:08X}\n"
            f"  Got:      0x{readback:08X}"
        )


@cocotb.test()
async def test_bar1_partial_write_upper_bytes(dut):
    """
    Test partial write to upper 2 bytes of a DWORD (first_be=0xC).

    This verifies that only bytes 2-3 are modified while bytes 0-1 are preserved.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    BAR1_HIT = 0b000010
    OFFSET = 0x04  # Use different offset from previous test

    # Step 1: Initialize with known pattern
    initial_value = 0xCAFEBABE
    await write_dword(bfm, BAR1_HIT, OFFSET, initial_value, tag=1)

    # Verify initial write
    readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=2)
    assert readback == initial_value, f"Initial write failed: got 0x{readback:08X}"

    # Step 2: Partial write to upper 2 bytes only (first_be=0xC)
    partial_value = 0x12340000
    await write_dword(bfm, BAR1_HIT, OFFSET, partial_value, tag=3, first_be=0xC)

    # Step 3: Read back and verify
    # Expected: 0x1234BABE (upper bytes updated, lower bytes preserved)
    readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=4)
    expected = 0x1234BABE

    if readback == expected:
        dut._log.info(f"PASS: Partial write (first_be=0xC) correct: 0x{readback:08X}")
    else:
        raise AssertionError(
            f"Partial write failed!\n"
            f"  Initial:  0x{initial_value:08X}\n"
            f"  Written:  0x{partial_value:08X} (first_be=0xC)\n"
            f"  Expected: 0x{expected:08X}\n"
            f"  Got:      0x{readback:08X}"
        )


@cocotb.test()
async def test_bar1_single_byte_writes(dut):
    """
    Test single byte writes with first_be=0x1, 0x2, 0x4, 0x8.

    Each byte position should be independently writable.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    BAR1_HIT = 0b000010
    OFFSET = 0x08

    # Initialize with zeros
    await write_dword(bfm, BAR1_HIT, OFFSET, 0x00000000, tag=1)

    # Write each byte position independently
    test_cases = [
        (0x1, 0x000000AA, 0x000000AA, "byte 0"),
        (0x2, 0x0000BB00, 0x0000BBAA, "byte 1"),
        (0x4, 0x00CC0000, 0x00CCBBAA, "byte 2"),
        (0x8, 0xDD000000, 0xDDCCBBAA, "byte 3"),
    ]

    tag = 2
    for first_be, write_val, expected, desc in test_cases:
        await write_dword(bfm, BAR1_HIT, OFFSET, write_val, tag=tag, first_be=first_be)
        tag += 1

        readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=tag)
        tag += 1

        if readback == expected:
            dut._log.info(f"PASS: Single byte write ({desc}, first_be=0x{first_be:X}): 0x{readback:08X}")
        else:
            raise AssertionError(
                f"Single byte write failed for {desc}!\n"
                f"  first_be: 0x{first_be:X}\n"
                f"  Written:  0x{write_val:08X}\n"
                f"  Expected: 0x{expected:08X}\n"
                f"  Got:      0x{readback:08X}"
            )

    dut._log.info("All single byte write tests passed")


@cocotb.test()
async def test_bar1_byte_enable_combinations(dut):
    """
    Test various byte enable combinations including non-contiguous patterns.

    For single DWORD TLPs to memory (BAR1 buffer), any first_be pattern is valid
    per PCIe spec, including non-contiguous patterns like 0x5 (bytes 0,2).
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    BAR1_HIT = 0b000010
    OFFSET = 0x0C

    # Test byte enable combinations including non-contiguous patterns
    # For BAR1 (memory buffer), all patterns should work
    test_cases = [
        # (initial, write_val, first_be, expected, description)
        (0xFFFFFFFF, 0x00AA00BB, 0x5, 0xFFAAFFBB, "bytes 0,2 (first_be=0x5)"),
        (0xFFFFFFFF, 0xCC00DD00, 0xA, 0xCCFFDDFF, "bytes 1,3 (first_be=0xA)"),
        (0x00000000, 0xAABBCCDD, 0x9, 0xAA0000DD, "bytes 0,3 (first_be=0x9)"),
        (0x12345678, 0x00000000, 0x6, 0x12000078, "bytes 1,2 (first_be=0x6)"),
    ]

    tag = 1
    for initial, write_val, first_be, expected, desc in test_cases:
        # Initialize
        await write_dword(bfm, BAR1_HIT, OFFSET, initial, tag=tag)
        tag += 1

        # Partial write
        await write_dword(bfm, BAR1_HIT, OFFSET, write_val, tag=tag, first_be=first_be)
        tag += 1

        # Verify
        readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=tag)
        tag += 1

        if readback == expected:
            dut._log.info(f"PASS: {desc}: 0x{readback:08X}")
        else:
            raise AssertionError(
                f"Byte enable combination failed: {desc}\n"
                f"  Initial:  0x{initial:08X}\n"
                f"  Written:  0x{write_val:08X} (first_be=0x{first_be:X})\n"
                f"  Expected: 0x{expected:08X}\n"
                f"  Got:      0x{readback:08X}"
            )

    dut._log.info("All byte enable combination tests passed")


# =============================================================================
# BAR0 Register Byte Enable Tests
# =============================================================================
# Test byte enables on actual registers (more complex due to register behavior)

@cocotb.test()
async def test_bar0_register_partial_write(dut):
    """
    Test partial write to BAR0 PASID_VAL register.

    PASID_VAL is a 20-bit register at offset 0x20, suitable for testing
    partial byte writes on a real register.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    BAR0_HIT = 0b000001
    REG_PASID_VAL = 0x20

    # Initialize PASID_VAL with known value
    initial_value = 0x000AAAAA  # 20-bit value, upper 12 bits reserved
    await write_dword(bfm, BAR0_HIT, REG_PASID_VAL, initial_value, tag=1)

    # Verify initial write (mask to 20 bits as upper bits are reserved)
    readback = await read_dword(bfm, BAR0_HIT, REG_PASID_VAL, tag=2)
    assert (readback & 0xFFFFF) == (initial_value & 0xFFFFF), \
        f"Initial write failed: got 0x{readback:08X}"

    # Partial write to lower byte only (first_be=0x1)
    # Write 0x55 to byte 0
    partial_value = 0x00000055
    await write_dword(bfm, BAR0_HIT, REG_PASID_VAL, partial_value, tag=3, first_be=0x1)

    # Expected: lower byte changed to 0x55, rest preserved
    # 0x000AAAAA -> 0x000AAA55 (masked to 20 bits)
    readback = await read_dword(bfm, BAR0_HIT, REG_PASID_VAL, tag=4)
    expected = 0x000AAA55 & 0xFFFFF

    assert (readback & 0xFFFFF) == expected, \
        f"BAR0 register partial write failed:\n" \
        f"  Initial:  0x{initial_value:08X}\n" \
        f"  Written:  0x{partial_value:08X} (first_be=0x1)\n" \
        f"  Expected: 0x{expected:08X}\n" \
        f"  Got:      0x{readback:08X}"

    dut._log.info(f"PASS: BAR0 register partial write: 0x{readback:08X}")


@cocotb.test()
async def test_byte_enable_zero_no_write(dut):
    """
    Test that first_be=0x0 results in no bytes being written.

    Per PCIe spec, a write with all byte enables disabled should not modify memory.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    BAR1_HIT = 0b000010
    OFFSET = 0x10

    # Initialize with known value
    initial_value = 0xABCD1234
    await write_dword(bfm, BAR1_HIT, OFFSET, initial_value, tag=1)

    # Verify
    readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=2)
    assert readback == initial_value, f"Initial write failed"

    # Write with first_be=0x0 (no bytes enabled)
    await write_dword(bfm, BAR1_HIT, OFFSET, 0x00000000, tag=3, first_be=0x0)

    # Value should be unchanged
    readback = await read_dword(bfm, BAR1_HIT, OFFSET, tag=4)

    if readback == initial_value:
        dut._log.info(f"PASS: first_be=0x0 correctly left memory unchanged")
    else:
        raise AssertionError(
            f"first_be=0x0 should not modify memory!\n"
            f"  Initial:  0x{initial_value:08X}\n"
            f"  Got:      0x{readback:08X}"
        )
