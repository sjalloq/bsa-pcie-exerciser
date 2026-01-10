#
# BSA PCIe Exerciser - Register Access Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Integration tests for BAR0 register access.

These tests validate that TLPs can flow through the full path:
PHY -> Depacketizer -> BAR Dispatcher -> Wishbone Bridge -> BSA Registers
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
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    dut.pcie_rst.value = 1
    await ClockCycles(dut.sys_clk, 10)
    dut.sys_rst.value = 0
    dut.pcie_rst.value = 0
    await ClockCycles(dut.sys_clk, 10)


async def write_bar0_register(bfm, offset, data):
    """
    Write a 32-bit value to a BAR0 register.

    Args:
        bfm: PCIeBFM instance
        offset: Register offset within BAR0
        data: 32-bit value to write
    """
    # Build Memory Write TLP
    # Note: Use BAR-relative address (offset only) since depacketizer applies mask
    data_bytes = data.to_bytes(4, 'little')
    beats = TLPBuilder.memory_write_32(
        address=offset,  # BAR-relative offset
        data_bytes=data_bytes,
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)  # BAR0

    # Small delay for write to complete
    await ClockCycles(bfm.clk, 5)


async def read_bar0_register(bfm, offset, tag=0):
    """
    Read a 32-bit value from a BAR0 register.

    Args:
        bfm: PCIeBFM instance
        offset: Register offset within BAR0
        tag: TLP tag

    Returns:
        32-bit register value, or None on timeout
    """
    # Build Memory Read TLP
    # Note: Use BAR-relative address (offset only) since depacketizer applies mask
    beats = TLPBuilder.memory_read_32(
        address=offset,  # BAR-relative offset
        length_dw=1,
        requester_id=0x0100,
        tag=tag,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)  # BAR0

    # Wait for completion
    cpl = await bfm.capture_tlp(timeout_cycles=200)
    if cpl is None:
        return None

    # Extract data from completion
    # LitePCIe format: Beat 1: [Data0 | DW2] - data is in upper 32 bits
    # PHY uses big-endian wire format, so we need to byte-swap the DWORD
    raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF
    # Byte-swap: convert from big-endian wire format to little-endian host format
    data = int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')
    return data


# =============================================================================
# Register Read Tests
# =============================================================================

@cocotb.test()
async def test_bar0_id_register_read(dut):
    """
    Read the ID register at offset 0x48.

    The ID register contains:
    - [31:16]: Device ID (0xED01)
    - [15:0]:  Vendor ID (0x13B5)
    Expected value: 0xED0113B5
    """
    # Start both clocks - sys at 125MHz (8ns), pcie at 100MHz (10ns)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    dut._log.info("Reading ID register at offset 0x48")

    data = await read_bar0_register(bfm, offset=0x48, tag=1)

    if data is None:
        raise AssertionError("Timeout waiting for completion - no response from BAR0")

    expected = 0xED0113B5  # Device ID << 16 | Vendor ID
    dut._log.info(f"ID register = 0x{data:08X} (expected 0x{expected:08X})")

    assert data == expected, f"ID register mismatch: got 0x{data:08X}, expected 0x{expected:08X}"

    dut._log.info("test_bar0_id_register_read PASSED")


@cocotb.test()
async def test_bar0_dmactl_write_read(dut):
    """
    Write and read back the DMACTL register at offset 0x08.

    Tests that register writes work correctly through the full path.
    Note: Bits [3:0] are auto-clearing (trigger), so we test the persistent bits.

    DMACTL bit fields (from ARM BSA Exerciser spec):
        [3:0]:   dmatxntrig      - Trigger DMA (write 1, auto-clears)
        [4]:     dmatxndir       - Direction (0=Read, 1=Write)
        [5]:     dmatxnsnoop     - Snoop attr (0=Snoop, 1=No-snoop)
        [6]:     dmapasiden      - PASID Enable
        [7]:     dmaIsPrivileged - Privileged access mode
        [8]:     dmaIsInstruction - Instruction access
        [9]:     dmaUseATCforTranslation - Use ATC
        [11:10]: dmaAddressType  - Address type (0=Untranslated, 2=Translated)
        [31:12]: Reserved
    """
    # Start both clocks - sys at 125MHz (8ns), pcie at 100MHz (10ns)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Write a value with persistent bits set (avoid trigger bits [3:0])
    # Set: no_snoop[5]=1, direction[4]=1 -> 0x30
    test_value = 0x00000030

    dut._log.info(f"Writing 0x{test_value:08X} to DMACTL (offset 0x08)")
    await write_bar0_register(bfm, offset=0x08, data=test_value)

    # Read back
    data = await read_bar0_register(bfm, offset=0x08, tag=2)

    if data is None:
        raise AssertionError("Timeout waiting for DMACTL read completion")

    dut._log.info(f"DMACTL read back = 0x{data:08X}")

    # Check that the persistent bits are set
    assert (data & 0x30) == 0x30, f"direction/no_snoop bits not set: got 0x{data:08X}, expected bits [5:4]=0x30"

    dut._log.info("test_bar0_dmactl_write_read PASSED")


@cocotb.test()
async def test_bar0_pasid_val_write_read(dut):
    """
    Write and read back the PASID_VAL register at offset 0x20.

    PASID_VAL contains the 20-bit PASID value to use for DMA/ATS operations.
    """
    # Start both clocks - sys at 125MHz (8ns), pcie at 100MHz (10ns)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Write a test PASID value (20 bits, max 0xFFFFF)
    test_pasid = 0x12345

    dut._log.info(f"Writing PASID value 0x{test_pasid:05X} to PASID_VAL (offset 0x20)")
    await write_bar0_register(bfm, offset=0x20, data=test_pasid)

    # Read back
    data = await read_bar0_register(bfm, offset=0x20, tag=3)

    if data is None:
        raise AssertionError("Timeout waiting for PASID_VAL read completion")

    dut._log.info(f"PASID_VAL read back = 0x{data:08X}")

    # Check the PASID value (lower 20 bits)
    read_pasid = data & 0xFFFFF
    assert read_pasid == test_pasid, \
        f"PASID mismatch: got 0x{read_pasid:05X}, expected 0x{test_pasid:05X}"

    dut._log.info("test_bar0_pasid_val_write_read PASSED")


@cocotb.test()
async def test_bar0_dma_bus_addr_write_read(dut):
    """
    Write and read back the DMA_BUS_ADDR registers at offsets 0x10 and 0x14.

    These are the low and high 32 bits of the 64-bit host bus address for DMA.
    """
    # Start both clocks - sys at 125MHz (8ns), pcie at 100MHz (10ns)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Write test addresses
    addr_lo = 0x12345678
    addr_hi = 0x00000001

    dut._log.info(f"Writing DMA_BUS_ADDR: 0x{addr_hi:08X}_{addr_lo:08X}")
    await write_bar0_register(bfm, offset=0x10, data=addr_lo)
    await write_bar0_register(bfm, offset=0x14, data=addr_hi)

    # Read back
    data_lo = await read_bar0_register(bfm, offset=0x10, tag=4)
    data_hi = await read_bar0_register(bfm, offset=0x14, tag=5)

    if data_lo is None or data_hi is None:
        raise AssertionError("Timeout waiting for DMA_BUS_ADDR read completion")

    dut._log.info(f"DMA_BUS_ADDR read back = 0x{data_hi:08X}_{data_lo:08X}")

    assert data_lo == addr_lo, f"DMA_BUS_ADDR_LO mismatch: got 0x{data_lo:08X}"
    assert data_hi == addr_hi, f"DMA_BUS_ADDR_HI mismatch: got 0x{data_hi:08X}"

    dut._log.info("test_bar0_dma_bus_addr_write_read PASSED")


@cocotb.test()
async def test_bar0_multiple_sequential_reads(dut):
    """
    Test multiple sequential register reads to verify tag handling.
    """
    # Start both clocks - sys at 125MHz (8ns), pcie at 100MHz (10ns)
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Read multiple registers with different tags
    registers = [
        (0x48, "ID"),
        (0x08, "DMACTL"),
        (0x20, "PASID_VAL"),
    ]

    for i, (offset, name) in enumerate(registers):
        data = await read_bar0_register(bfm, offset=offset, tag=i+10)
        if data is None:
            raise AssertionError(f"Timeout reading {name} register")
        dut._log.info(f"{name} (offset 0x{offset:02X}) = 0x{data:08X}")

    dut._log.info("test_bar0_multiple_sequential_reads PASSED")
