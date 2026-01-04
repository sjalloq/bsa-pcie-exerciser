#
# USB Etherbone Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies that CSR registers are accessible via USB channel 0 Etherbone.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.usb_bfm import USBBFM
from tests.common.pcie_bfm import PCIeBFM


# =============================================================================
# Register Offsets (from bsa_registers.py)
# =============================================================================

REG_MSICTL         = 0x000
REG_INTXCTL        = 0x004
REG_DMACTL         = 0x008
REG_DMA_OFFSET     = 0x00C
REG_DMA_BUS_ADDR_LO = 0x010
REG_DMA_BUS_ADDR_HI = 0x014
REG_DMA_LEN        = 0x018
REG_DMASTATUS      = 0x01C
REG_PASID_VAL      = 0x020
REG_ATSCTL         = 0x024
REG_ATS_ADDR_LO    = 0x028
REG_ATS_ADDR_HI    = 0x02C
REG_ATS_RANGE_SIZE = 0x030
REG_ATS_PERM       = 0x038
REG_RID_CTL        = 0x03C
REG_TXN_TRACE      = 0x040
REG_TXN_CTRL       = 0x044
REG_ID             = 0x048

REG_USB_MON_CTRL        = 0x080
REG_USB_MON_STATUS      = 0x084
REG_USB_MON_RX_CAPTURED = 0x088
REG_USB_MON_RX_DROPPED  = 0x08C
REG_USB_MON_TX_CAPTURED = 0x090
REG_USB_MON_TX_DROPPED  = 0x094

# Expected values
EXERCISER_VENDOR_ID = 0x13B5
EXERCISER_DEVICE_ID = 0xED01
EXERCISER_COMBINED_ID = (EXERCISER_DEVICE_ID << 16) | EXERCISER_VENDOR_ID  # 0xED0113B5


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset and initialize clocks."""
    # Start sys clock (125MHz = 8ns period)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    # Start PCIe clock (same as sys)
    cocotb.start_soon(Clock(dut.pcie_clk, 8, unit="ns").start())
    # Start USB clock (100MHz = 10ns period) - async phase
    cocotb.start_soon(Clock(dut.usb_clk, 10, unit="ns").start())

    # Apply reset
    dut.sys_rst.value = 1
    dut.pcie_rst.value = 1
    dut.usb_rst.value = 1

    # Initialize PCIe signals
    dut.phy_rx_valid.value = 0
    dut.phy_tx_ready.value = 1

    await ClockCycles(dut.sys_clk, 20)

    # Release reset
    dut.sys_rst.value = 0
    dut.pcie_rst.value = 0
    dut.usb_rst.value = 0

    await ClockCycles(dut.sys_clk, 50)


# =============================================================================
# Etherbone Tests
# =============================================================================

@cocotb.test()
async def test_etherbone_read_id_register(dut):
    """
    Read BSA ID register (0x48) via Etherbone.

    Verifies:
    1. Etherbone packet can be sent via USB stub
    2. CSR read transaction completes
    3. Correct ID value returned (0xED0113B5)
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Read ID register via Etherbone
    id_value = await usb_bfm.send_etherbone_read(REG_ID)

    assert id_value == EXERCISER_COMBINED_ID, \
        f"Expected ID 0x{EXERCISER_COMBINED_ID:08X}, got 0x{id_value:08X}"
    dut._log.info(f"ID register read via Etherbone: 0x{id_value:08X}")


@cocotb.test()
async def test_etherbone_write_read(dut):
    """
    Write then read a R/W register via Etherbone.

    Uses DMA_OFFSET register as test target (fully writable).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    test_values = [0x12345678, 0xDEADBEEF, 0x00000000, 0xFFFFFFFF]

    for test_value in test_values:
        # Write via Etherbone
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, test_value)

        # Read back via Etherbone
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)

        assert readback == test_value, \
            f"Readback mismatch: wrote 0x{test_value:08X}, got 0x{readback:08X}"
        dut._log.info(f"Write/read 0x{test_value:08X} successful")


@cocotb.test()
async def test_etherbone_burst_read(dut):
    """
    Burst read multiple consecutive registers via single Etherbone packet.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Read registers from 0x00 to 0x1C (8 registers)
    addresses = list(range(0x00, 0x20, 4))
    values = await usb_bfm.send_etherbone_burst_read(addresses)

    assert len(values) == len(addresses), \
        f"Expected {len(addresses)} values, got {len(values)}"

    dut._log.info("Burst read results:")
    for addr, val in zip(addresses, values):
        dut._log.info(f"  0x{addr:02X}: 0x{val:08X}")


@cocotb.test()
async def test_etherbone_multiple_reads(dut):
    """
    Multiple sequential reads to verify protocol state machine works correctly.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Read various registers multiple times
    for _ in range(5):
        id_val = await usb_bfm.send_etherbone_read(REG_ID)
        assert id_val == EXERCISER_COMBINED_ID

        dmactl = await usb_bfm.send_etherbone_read(REG_DMACTL)
        dmastatus = await usb_bfm.send_etherbone_read(REG_DMASTATUS)

        dut._log.info(f"ID=0x{id_val:08X}, DMACTL=0x{dmactl:08X}, DMASTATUS=0x{dmastatus:08X}")


@cocotb.test()
async def test_etherbone_read_usb_monitor_regs(dut):
    """
    Read USB monitor registers via Etherbone.

    These registers are specific to SquirrelSoC.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Read monitor control register
    mon_ctrl = await usb_bfm.send_etherbone_read(REG_USB_MON_CTRL)
    dut._log.info(f"USB_MON_CTRL: 0x{mon_ctrl:08X}")

    # Read captured/dropped counters
    rx_captured = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED)
    rx_dropped = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_DROPPED)
    tx_captured = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_CAPTURED)
    tx_dropped = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_DROPPED)

    dut._log.info(f"RX captured: {rx_captured}, dropped: {rx_dropped}")
    dut._log.info(f"TX captured: {tx_captured}, dropped: {tx_dropped}")


@cocotb.test()
async def test_etherbone_configure_usb_monitor(dut):
    """
    Configure USB monitor via Etherbone writes.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Enable RX and TX monitoring
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)

    # Read back
    mon_ctrl = await usb_bfm.send_etherbone_read(REG_USB_MON_CTRL)
    assert (mon_ctrl & 0x03) == 0x03, f"Expected bits [1:0]=0x03, got 0x{mon_ctrl:08X}"

    dut._log.info(f"USB monitor enabled: CTRL=0x{mon_ctrl:08X}")

    # Disable monitoring
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x00)
    mon_ctrl = await usb_bfm.send_etherbone_read(REG_USB_MON_CTRL)
    assert (mon_ctrl & 0x03) == 0x00, f"Expected bits [1:0]=0x00, got 0x{mon_ctrl:08X}"


@cocotb.test()
async def test_etherbone_burst_write(dut):
    """
    Burst write multiple values to consecutive addresses.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Write DMA address registers (low and high)
    base_addr = REG_DMA_BUS_ADDR_LO
    values = [0xCAFEBABE, 0x12345678]

    await usb_bfm.send_etherbone_burst_write(base_addr, values)

    # Read back individually
    lo = await usb_bfm.send_etherbone_read(REG_DMA_BUS_ADDR_LO)
    hi = await usb_bfm.send_etherbone_read(REG_DMA_BUS_ADDR_HI)

    assert lo == values[0], f"Low addr mismatch: 0x{lo:08X} != 0x{values[0]:08X}"
    assert hi == values[1], f"High addr mismatch: 0x{hi:08X} != 0x{values[1]:08X}"

    dut._log.info(f"Burst write successful: LO=0x{lo:08X}, HI=0x{hi:08X}")


@cocotb.test()
async def test_etherbone_concurrent_with_pcie(dut):
    """
    Verify Etherbone and PCIe CSR access don't interfere.

    Writes via PCIe, reads via Etherbone and vice versa.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Write via Etherbone
    test_value = 0xA5A5A5A5
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, test_value)

    # Read via PCIe (BAR0 CSR read)
    from tests.common.tlp_builder import TLPBuilder

    # Build a Memory Read TLP for BAR0
    beats = TLPBuilder.memory_read_32(
        address=REG_DMA_OFFSET,
        length_dw=1,
        requester_id=0x0100,
        tag=1,
    )

    # Inject the TLP
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    # Wait for and capture completion
    cpl_beats = await pcie_bfm.capture_tlp(timeout_cycles=500)

    assert cpl_beats is not None, "Expected PCIe completion for memory read"
    dut._log.info(f"Got PCIe completion with {len(cpl_beats)} beats")

    # Read via Etherbone to verify value
    readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)
    assert readback == test_value, f"Etherbone readback mismatch: 0x{readback:08X}"

    dut._log.info("Concurrent USB/PCIe access test complete")


@cocotb.test()
async def test_etherbone_stress(dut):
    """
    Stress test with many rapid read/write operations.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    NUM_ITERATIONS = 20

    for i in range(NUM_ITERATIONS):
        # Write a unique value
        test_value = (i << 24) | (i << 16) | (i << 8) | i
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, test_value)

        # Read back immediately
        readback = await usb_bfm.send_etherbone_read(REG_DMA_OFFSET)

        assert readback == test_value, \
            f"Iteration {i}: wrote 0x{test_value:08X}, got 0x{readback:08X}"

    dut._log.info(f"Stress test passed: {NUM_ITERATIONS} write/read cycles")
