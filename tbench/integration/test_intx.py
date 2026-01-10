#
# BSA PCIe Exerciser - INTx Controller Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Integration tests for the Legacy INTx Controller.

The INTx controller handles legacy interrupt assertion/deassertion via
the Xilinx 7-series PCIe cfg_interrupt interface. Software controls
the interrupt state via the INTXCTL register.
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

REG_INTXCTL = 0x04   # INTx Control: [0]=assert


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


# =============================================================================
# INTx Controller Tests
# =============================================================================

@cocotb.test()
async def test_intx_assert(dut):
    """
    Test INTx assertion via INTXCTL register.

    Writes INTXCTL[0]=1 to assert legacy interrupt and verifies:
    1. Register value is stored correctly
    2. INTx controller processes the assertion request
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    dut._log.info("Testing INTx assertion")

    # Read initial state (should be 0 after reset)
    initial_val = await read_bar0_register(bfm, REG_INTXCTL, tag=1)
    dut._log.info(f"Initial INTXCTL = 0x{initial_val:08X}")

    assert (initial_val & 0x1) == 0, "INTXCTL[0] should be 0 after reset"

    # Assert INTx by setting bit 0
    await write_bar0_register(bfm, REG_INTXCTL, 0x01)

    # Wait for controller to process
    await ClockCycles(bfm.clk, 20)

    # Read back and verify register
    intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=2)
    dut._log.info(f"INTXCTL after assert = 0x{intxctl:08X}")

    assert (intxctl & 0x1) == 1, "INTXCTL[0] should be 1 after assertion"

    # Verify PHY latched state - this tests the actual wiring to the PHY
    intx_asserted = int(dut.intx_asserted.value)
    dut._log.info(f"PHY INTx asserted state = {intx_asserted}")

    assert intx_asserted == 1, "PHY intx_asserted should be 1 after assertion"

    dut._log.info("test_intx_assert PASSED")


@cocotb.test()
async def test_intx_deassert(dut):
    """
    Test INTx deassertion via INTXCTL register.

    First asserts INTx, then deasserts by clearing INTXCTL[0].
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    dut._log.info("Testing INTx deassertion")

    # First assert INTx
    await write_bar0_register(bfm, REG_INTXCTL, 0x01)
    await ClockCycles(bfm.clk, 20)

    intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=1)
    assert (intxctl & 0x1) == 1, "INTXCTL[0] should be 1"
    dut._log.info("INTx asserted")

    # Now deassert by clearing bit 0
    await write_bar0_register(bfm, REG_INTXCTL, 0x00)
    await ClockCycles(bfm.clk, 20)

    # Read back and verify register
    intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=2)
    dut._log.info(f"INTXCTL after deassert = 0x{intxctl:08X}")

    assert (intxctl & 0x1) == 0, "INTXCTL[0] should be 0 after deassertion"

    # Verify PHY latched state - should be 0 (deasserted)
    intx_asserted = int(dut.intx_asserted.value)
    dut._log.info(f"PHY INTx asserted state = {intx_asserted}")

    assert intx_asserted == 0, "PHY intx_asserted should be 0 after deassertion"

    dut._log.info("test_intx_deassert PASSED")


@cocotb.test()
async def test_intx_level_behavior(dut):
    """
    Test INTx level-triggered behavior.

    Verifies that:
    1. INTx stays asserted until explicitly cleared
    2. Multiple writes don't affect stable state
    3. State transitions are clean
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    dut._log.info("Testing INTx level-triggered behavior")

    # Assert INTx
    await write_bar0_register(bfm, REG_INTXCTL, 0x01)
    await ClockCycles(bfm.clk, 20)

    # Verify it stays asserted (read multiple times)
    for i in range(3):
        await ClockCycles(bfm.clk, 10)
        intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=i+10)
        assert (intxctl & 0x1) == 1, f"INTx should stay asserted (read {i+1})"

    # Verify PHY latched state matches
    assert int(dut.intx_asserted.value) == 1, "PHY intx_asserted should be 1"
    dut._log.info("INTx stays asserted - PASS")

    # Write again (should have no effect, already asserted)
    await write_bar0_register(bfm, REG_INTXCTL, 0x01)
    await ClockCycles(bfm.clk, 10)

    intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=20)
    assert (intxctl & 0x1) == 1, "Re-asserting should maintain state"
    dut._log.info("Re-assertion maintains state - PASS")

    # Deassert
    await write_bar0_register(bfm, REG_INTXCTL, 0x00)
    await ClockCycles(bfm.clk, 20)

    # Verify stays deasserted
    for i in range(3):
        await ClockCycles(bfm.clk, 10)
        intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=30+i)
        assert (intxctl & 0x1) == 0, f"INTx should stay deasserted (read {i+1})"

    # Verify PHY latched state matches
    assert int(dut.intx_asserted.value) == 0, "PHY intx_asserted should be 0"
    dut._log.info("INTx stays deasserted - PASS")

    # Rapid toggling test
    for i in range(5):
        await write_bar0_register(bfm, REG_INTXCTL, 0x01)
        await ClockCycles(bfm.clk, 5)
        await write_bar0_register(bfm, REG_INTXCTL, 0x00)
        await ClockCycles(bfm.clk, 5)

    # Final state should be deasserted
    intxctl = await read_bar0_register(bfm, REG_INTXCTL, tag=50)
    assert (intxctl & 0x1) == 0, "Final state should be deasserted after toggling"

    dut._log.info("Rapid toggling handled correctly - PASS")
    dut._log.info("test_intx_level_behavior PASSED")
