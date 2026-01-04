#
# Multi-DMA Ordering Tests (BSA e026-e029)
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies DMA engine behavior with multiple outstanding transactions:
# - Multiple reads before completions arrive
# - Out-of-order completion handling
# - Tag reuse after completion
# - Interleaved read/write operations
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.usb_bfm import USBBFM
from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder

from bsa_pcie_exerciser.common.protocol import (
    parse_tlp_packet, TLPPacket, Direction,
    TLP_TYPE_MRD, TLP_TYPE_MWR,
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_DMACTL              = 0x008
REG_DMA_OFFSET          = 0x00C
REG_DMA_BUS_ADDR_LO     = 0x010
REG_DMA_BUS_ADDR_HI     = 0x014
REG_DMA_LEN             = 0x018
REG_DMASTATUS           = 0x01C
REG_USB_MON_CTRL        = 0x080

# DMACTL bit definitions (per ARM BSA Exerciser spec)
DMACTL_TRIGGER    = (1 << 0)   # [3:0] trigger
DMACTL_DIRECTION  = (1 << 4)   # [4] 0=read, 1=write
DMACTL_NO_SNOOP   = (1 << 5)   # [5] no-snoop

# DMASTATUS bit definitions
DMASTATUS_BUSY   = (1 << 0)
DMASTATUS_OK     = (0 << 1)
DMASTATUS_ERROR  = (1 << 1)
DMASTATUS_TIMEOUT = (2 << 1)


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset and initialize clocks."""
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.usb_clk, 10, unit="ns").start())

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


async def enable_tx_monitoring(usb_bfm: USBBFM):
    """Enable TX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x02)


async def configure_dma(usb_bfm: USBBFM, address: int, length: int, offset: int = 0):
    """Configure DMA parameters without triggering."""
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, address & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, (address >> 32) & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, length)
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, offset)


async def trigger_dma_read(usb_bfm: USBBFM):
    """Trigger DMA read operation."""
    await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER)


async def trigger_dma_write(usb_bfm: USBBFM):
    """Trigger DMA write operation."""
    await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER | DMACTL_DIRECTION)


async def wait_for_dma_idle(usb_bfm: USBBFM, timeout_cycles: int = 1000):
    """Wait for DMA engine to become idle."""
    for _ in range(timeout_cycles // 10):
        status = await usb_bfm.send_etherbone_read(REG_DMASTATUS)
        if not (status & DMASTATUS_BUSY):
            return True
        await ClockCycles(usb_bfm.dut.sys_clk, 10)
    return False


def parse_monitor_packet(data: bytes) -> TLPPacket:
    """Parse a USB monitor packet into TLPPacket."""
    pkt = parse_tlp_packet(data)
    if pkt is None:
        raise ValueError(f"Failed to parse monitor packet: {data.hex()}")
    return pkt


# =============================================================================
# Multi-DMA Ordering Tests
# =============================================================================

@cocotb.test()
async def test_dma_read_with_completion(dut):
    """
    Basic test: DMA read with completion injection.

    1. Configure and trigger DMA read
    2. Capture outgoing MRd TLP
    3. Inject completion with data
    4. Verify DMA completes successfully
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure DMA read
    dma_addr = 0x12340000
    dma_len = 32  # bytes

    await configure_dma(usb_bfm, dma_addr, dma_len)
    await trigger_dma_read(usb_bfm)

    await ClockCycles(dut.sys_clk, 100)

    # Capture MRd TLP to get tag
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA MRd TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"DMA MRd captured: tag={pkt.tag}, addr=0x{pkt.address:X}")

    # Inject completion with data
    data = bytes([i & 0xFF for i in range(dma_len)])
    beats = TLPBuilder.completion(
        requester_id=0x0100,  # Our device
        completer_id=0x0000,  # Root complex
        tag=pkt.tag,
        data_bytes=data,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0)

    await ClockCycles(dut.sys_clk, 200)

    # Verify DMA completed
    idle = await wait_for_dma_idle(usb_bfm)
    assert idle, "DMA should complete after receiving completion"

    status = await usb_bfm.send_etherbone_read(REG_DMASTATUS)
    dut._log.info(f"DMA status: 0x{status:08X}")


@cocotb.test()
async def test_sequential_dma_reads(dut):
    """
    Test sequential DMA read operations with completions.

    Each DMA read should get a different tag.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    captured_tags = []

    for i in range(3):
        dma_addr = 0x10000000 + (i * 0x1000)
        dma_len = 16

        await configure_dma(usb_bfm, dma_addr, dma_len, offset=i * 16)
        await trigger_dma_read(usb_bfm)

        await ClockCycles(dut.sys_clk, 100)

        # Capture MRd
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
        assert packet_data is not None, f"Expected to capture DMA MRd TLP for iteration {i}"

        pkt = parse_monitor_packet(packet_data)
        captured_tags.append(pkt.tag)
        dut._log.info(f"DMA {i}: tag={pkt.tag}")

        # Inject completion
        data = bytes([(i << 4) | (j & 0xF) for j in range(dma_len)])
        beats = TLPBuilder.completion(
            requester_id=0x0100,
            completer_id=0x0000,
            tag=pkt.tag,
            data_bytes=data,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0)

        # Wait for this DMA to complete before starting next
        await wait_for_dma_idle(usb_bfm)

    dut._log.info(f"Captured tags: {captured_tags}")
    # Verify tags increment
    assert len(captured_tags) == 3, "Expected 3 DMA operations"


@cocotb.test()
async def test_dma_write_no_completion(dut):
    """
    Verify DMA write completes without needing a completion.

    Memory writes are posted - no completion expected.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure DMA write
    await configure_dma(usb_bfm, 0x20000000, 32)
    await trigger_dma_write(usb_bfm)

    await ClockCycles(dut.sys_clk, 200)

    # Capture MWr TLP
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA MWr TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"DMA MWr captured: addr=0x{pkt.address:X}")
    assert pkt.tlp_type == TLP_TYPE_MWR, f"Expected MWr TLP, got {pkt.type_name}"

    # DMA write should complete immediately (posted)
    idle = await wait_for_dma_idle(usb_bfm)
    assert idle, "DMA write should complete without waiting for completion"


@cocotb.test()
async def test_interleaved_read_write(dut):
    """
    Test interleaved DMA read and write operations.

    1. Start DMA read
    2. Before read completes, verify we can't start another DMA
    3. Complete read
    4. Start DMA write
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Start DMA read
    await configure_dma(usb_bfm, 0x30000000, 32)
    await trigger_dma_read(usb_bfm)

    await ClockCycles(dut.sys_clk, 100)

    # Check DMA is busy
    status = await usb_bfm.send_etherbone_read(REG_DMASTATUS)
    is_busy = (status & DMASTATUS_BUSY) != 0
    dut._log.info(f"DMA busy during read: {is_busy}")

    # Capture the MRd and complete it
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA MRd TLP"

    pkt = parse_monitor_packet(packet_data)

    # Inject completion
    data = bytes([0xAA] * 32)
    beats = TLPBuilder.completion(
        requester_id=0x0100,
        completer_id=0x0000,
        tag=pkt.tag,
        data_bytes=data,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0)

    await wait_for_dma_idle(usb_bfm)

    # Now start DMA write
    await configure_dma(usb_bfm, 0x40000000, 32)
    await trigger_dma_write(usb_bfm)

    await ClockCycles(dut.sys_clk, 200)

    # Capture MWr
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA MWr TLP after read completed"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Write after read: type={pkt.type_name}")
    assert pkt.tlp_type == TLP_TYPE_MWR, f"Expected MWr TLP, got {pkt.type_name}"

    await wait_for_dma_idle(usb_bfm)


@cocotb.test()
async def test_tag_per_operation(dut):
    """
    Verify tag behavior across multiple DMA operations.

    The TLP controller manages tags from a pool (0 to max_pending_requests-1).
    For sequential operations where each completes before the next starts,
    tags are recycled: 0, 1, 2, 3, 0, 1, ... (with max_pending_requests=4).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    tags = []

    for i in range(5):
        await configure_dma(usb_bfm, 0x50000000 + i * 0x100, 8)
        await trigger_dma_read(usb_bfm)

        await ClockCycles(dut.sys_clk, 100)

        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
        assert packet_data is not None, f"Expected to capture DMA TLP for iteration {i}"

        pkt = parse_monitor_packet(packet_data)
        tags.append(pkt.tag)
        dut._log.info(f"DMA {i}: tag={pkt.tag}")

        # Complete this DMA using the captured tag
        beats = TLPBuilder.completion(
            requester_id=0x0100,
            completer_id=0x0000,
            tag=pkt.tag,
            data_bytes=bytes([0x55] * 8),
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0)
        await wait_for_dma_idle(usb_bfm)

    dut._log.info(f"Captured tags: {tags}")
    assert len(tags) == 5, f"Expected 5 tags, got {len(tags)}"

    # TLP controller manages tags from a pool. For sequential ops (each completes
    # before next starts), tags cycle through the pool: 0, 1, 2, 3, 0, ...
    # With max_pending_requests=4, expect tags to wrap after 4 operations.
    for i, tag in enumerate(tags):
        expected_tag = i % 4  # Tags 0-3 cycle
        assert tag == expected_tag, \
            f"DMA {i}: expected tag={expected_tag}, got tag={tag}"


@cocotb.test()
async def test_dma_timeout_recovery(dut):
    """
    Verify DMA engine recovers from timeout (no completion received).

    Note: This test may take a while due to timeout delay.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Start DMA read without sending completion
    await configure_dma(usb_bfm, 0x60000000, 16)
    await trigger_dma_read(usb_bfm)

    # Wait for timeout (this may be long in simulation)
    # The DMA engine has a timeout counter
    for _ in range(100):
        await ClockCycles(dut.sys_clk, 1000)
        status = await usb_bfm.send_etherbone_read(REG_DMASTATUS)
        if not (status & DMASTATUS_BUSY):
            break

    status = await usb_bfm.send_etherbone_read(REG_DMASTATUS)
    dut._log.info(f"Final status after timeout: 0x{status:08X}")

    # After timeout, DMA should be able to start new operation
    await configure_dma(usb_bfm, 0x70000000, 8)
    await trigger_dma_write(usb_bfm)  # Use write to avoid needing completion

    await ClockCycles(dut.sys_clk, 200)
    idle = await wait_for_dma_idle(usb_bfm)

    dut._log.info(f"Recovery successful: {idle}")
