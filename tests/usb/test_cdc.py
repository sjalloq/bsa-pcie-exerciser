#
# Clock Domain Crossing Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies reliable operation across sys/usb clock domain boundary
# with various phase relationships and edge cases.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer, RisingEdge

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.usb_bfm import USBBFM
from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder


# =============================================================================
# Register Offsets
# =============================================================================

REG_DMA_OFFSET = 0x00C
REG_ID = 0x048
REG_USB_MON_CTRL = 0x080


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut, usb_phase_ps=0):
    """
    Reset and initialize clocks with optional USB clock phase offset.

    Args:
        usb_phase_ps: Phase offset of USB clock relative to sys clock in picoseconds
    """
    # Start sys clock (125MHz = 8ns period)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    # Start PCIe clock
    cocotb.start_soon(Clock(dut.pcie_clk, 8, unit="ns").start())

    # Apply phase offset to USB clock
    if usb_phase_ps > 0:
        await Timer(usb_phase_ps, unit="ps")

    # Start USB clock (100MHz = 10ns period)
    cocotb.start_soon(Clock(dut.usb_clk, 10, unit="ns").start())

    # Apply reset
    dut.sys_rst.value = 1
    dut.pcie_rst.value = 1
    dut.usb_rst.value = 1

    dut.phy_rx_valid.value = 0
    dut.phy_tx_ready.value = 1

    await ClockCycles(dut.sys_clk, 20)

    dut.sys_rst.value = 0
    dut.pcie_rst.value = 0
    dut.usb_rst.value = 0

    await ClockCycles(dut.sys_clk, 50)


# =============================================================================
# CDC Tests
# =============================================================================

@cocotb.test()
async def test_cdc_basic_transfer(dut):
    """
    Basic CDC test - verify data crosses clock domain correctly.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Simple read/write should cross CDC correctly
    test_value = 0xCAFEBABE
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, test_value)
    readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)

    assert readback == test_value, f"CDC failure: 0x{readback:08X} != 0x{test_value:08X}"
    dut._log.info("Basic CDC transfer passed")


@cocotb.test()
async def test_cdc_phase_0(dut):
    """Test with USB clock at 0 degree phase offset."""
    await reset_dut(dut, usb_phase_ps=0)
    usb_bfm = USBBFM(dut)

    # Run multiple transactions
    for i in range(10):
        val = 0x11111111 * (i + 1)
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
        assert readback == val, f"Phase 0 failure at iteration {i}"

    dut._log.info("CDC phase 0 test passed")


@cocotb.test()
async def test_cdc_phase_90(dut):
    """Test with USB clock at ~90 degree phase offset (2.5ns for 10ns period)."""
    await reset_dut(dut, usb_phase_ps=2500)
    usb_bfm = USBBFM(dut)

    for i in range(10):
        val = 0x22222222 * (i + 1) & 0xFFFFFFFF
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
        assert readback == val, f"Phase 90 failure at iteration {i}"

    dut._log.info("CDC phase 90 test passed")


@cocotb.test()
async def test_cdc_phase_180(dut):
    """Test with USB clock at ~180 degree phase offset (5ns for 10ns period)."""
    await reset_dut(dut, usb_phase_ps=5000)
    usb_bfm = USBBFM(dut)

    for i in range(10):
        val = 0x33333333 * (i + 1) & 0xFFFFFFFF
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
        assert readback == val, f"Phase 180 failure at iteration {i}"

    dut._log.info("CDC phase 180 test passed")


@cocotb.test()
async def test_cdc_phase_270(dut):
    """Test with USB clock at ~270 degree phase offset (7.5ns for 10ns period)."""
    await reset_dut(dut, usb_phase_ps=7500)
    usb_bfm = USBBFM(dut)

    for i in range(10):
        val = 0x44444444 * (i + 1) & 0xFFFFFFFF
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
        assert readback == val, f"Phase 270 failure at iteration {i}"

    dut._log.info("CDC phase 270 test passed")


@cocotb.test()
async def test_cdc_rapid_transactions(dut):
    """
    Rapid back-to-back transactions stress test CDC FIFOs.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Burst of writes followed by reads
    values = [0x10000000 + i for i in range(8)]

    # Write all values rapidly
    for val in values:
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)

    # Read back final value
    readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
    assert readback == values[-1], f"Final value mismatch: 0x{readback:08X}"

    dut._log.info("Rapid transactions test passed")


@cocotb.test()
async def test_cdc_alternating_direction(dut):
    """
    Rapidly alternating read/write to stress bidirectional CDC.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    for i in range(20):
        # Write
        val = (i << 20) | 0xABCDE
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)

        # Read back immediately
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
        assert readback == val, f"Alternating test failure at {i}: 0x{readback:08X} != 0x{val:08X}"

    dut._log.info("Alternating direction test passed")


@cocotb.test()
async def test_cdc_burst_with_monitor(dut):
    """
    Test CDC with monitor stream active (additional CDC traffic).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable both RX and TX monitoring (adds CDC traffic)
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)

    # Generate some PCIe traffic (will go through monitor CDC)
    for i in range(5):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 20)

    # Meanwhile, do Etherbone transactions (different CDC path)
    for i in range(5):
        val = 0x50000000 + i
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, val)
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
        assert readback == val

    dut._log.info("Burst with monitor test passed")


@cocotb.test()
async def test_cdc_id_register_stability(dut):
    """
    Read ID register many times - should always return same value.

    Tests CDC stability for read-only registers.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    expected_id = 0xED0113B5  # EXERCISER_COMBINED_ID

    for i in range(50):
        id_val = await usb_bfm.send_etherbone_read(REG_ID)
        assert id_val == expected_id, f"ID stability failure at {i}: 0x{id_val:08X}"

    dut._log.info("ID register stability test passed")
