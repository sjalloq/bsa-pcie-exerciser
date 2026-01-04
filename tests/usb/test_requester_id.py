#
# Requester ID Override Tests (BSA e001)
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies DMA engine uses overridden requester ID for ACS source validation
# testing per BSA test e001.
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
REG_RID_CTL             = 0x03C

REG_USB_MON_CTRL        = 0x080
REG_USB_MON_TX_CAPTURED = 0x090
REG_USB_MON_TX_DROPPED  = 0x094

# DMACTL bit definitions (per ARM BSA Exerciser spec)
DMACTL_TRIGGER    = (1 << 0)   # [3:0] trigger
DMACTL_DIRECTION  = (1 << 4)   # [4] 0=read, 1=write

# DMASTATUS bit definitions
DMASTATUS_BUSY = (1 << 0)


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
    """Enable TX monitoring via USB_MON_CTL register."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x02)  # TX enable


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


def extract_requester_id(pkt: TLPPacket) -> int:
    """Extract requester ID from TLP packet."""
    # Requester ID is in DW1[31:16] for MRd/MWr
    return pkt.req_id


# =============================================================================
# Requester ID Override Tests
# =============================================================================

@cocotb.test()
async def test_requester_id_override_dma_read(dut):
    """
    Verify DMA read TLP uses overridden requester ID when enabled.

    BSA Test: e001 (P2P ACS Source Validation)

    1. Set RID_CTL with spoofed requester ID and enable bit
    2. Trigger DMA read
    3. Capture outgoing TLP via TX monitor
    4. Verify requester ID in TLP matches override value
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure spoofed requester ID
    # RID_CTL: [15:0] = requester ID value, [31] = enable override
    spoofed_rid = 0xBEEF
    rid_ctl_val = (1 << 31) | spoofed_rid  # Enable + value
    await usb_bfm.send_etherbone_write(REG_RID_CTL, rid_ctl_val)

    # Configure DMA for read operation
    dma_addr = 0x12340000
    dma_len = 64  # bytes

    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, dma_addr & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, dma_len)
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, 0)

    # Trigger DMA read (direction=0)
    await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER)

    # Wait for DMA to generate TLP
    await ClockCycles(dut.sys_clk, 200)

    # Capture monitor packet
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected TX TLP to be captured"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: type={pkt.type_name}, req_id=0x{pkt.req_id:04X}")

    assert pkt.tlp_type == TLP_TYPE_MRD, f"Expected MRd, got {pkt.type_name}"
    assert pkt.direction == Direction.TX, "Expected TX direction"
    assert pkt.req_id == spoofed_rid, \
        f"Expected spoofed RID 0x{spoofed_rid:04X}, got 0x{pkt.req_id:04X}"


@cocotb.test()
async def test_requester_id_override_dma_write(dut):
    """
    Verify DMA write TLP uses overridden requester ID when enabled.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure spoofed requester ID
    spoofed_rid = 0xDEAD
    rid_ctl_val = (1 << 31) | spoofed_rid
    await usb_bfm.send_etherbone_write(REG_RID_CTL, rid_ctl_val)

    # Configure DMA for write operation
    dma_addr = 0x56780000
    dma_len = 32  # bytes

    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, dma_addr & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, dma_len)
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, 0)

    # Verify monitor is enabled before trigger
    mon_ctrl = await usb_bfm.send_etherbone_read(REG_USB_MON_CTRL)
    dut._log.info(f"USB_MON_CTRL = 0x{mon_ctrl:08X}")

    # Trigger DMA write (direction=1)
    dut._log.info(f"Triggering DMA write to addr=0x{dma_addr:08X}, len={dma_len}")
    await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER | DMACTL_DIRECTION)

    # Wait for DMA to generate TLP
    await ClockCycles(dut.sys_clk, 200)

    # Check DMA status
    status = await usb_bfm.send_etherbone_read(REG_DMASTATUS)
    dut._log.info(f"DMA status after trigger: 0x{status:08X}")

    # Check TX monitor statistics
    tx_captured = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_CAPTURED)
    tx_dropped = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_DROPPED)
    dut._log.info(f"TX monitor stats: captured={tx_captured}, dropped={tx_dropped}")

    # Capture monitor packet
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
    dut._log.info(f"Monitor packet received: {packet_data is not None}, data={packet_data.hex() if packet_data else 'None'}")

    assert packet_data is not None, "Expected TX TLP to be captured"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: type={pkt.type_name}, req_id=0x{pkt.req_id:04X}")

    assert pkt.tlp_type == TLP_TYPE_MWR, f"Expected MWr, got {pkt.type_name}"
    assert pkt.direction == Direction.TX, "Expected TX direction"
    assert pkt.req_id == spoofed_rid, \
        f"Expected spoofed RID 0x{spoofed_rid:04X}, got 0x{pkt.req_id:04X}"


@cocotb.test()
async def test_requester_id_override_disabled(dut):
    """
    Verify DMA uses device's real requester ID when override is disabled.

    1. Set RID_CTL with value but leave enable bit cleared
    2. Trigger DMA
    3. Verify TLP uses PHY's native ID (not the override value)
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure RID value but DON'T set enable bit
    spoofed_rid = 0xBAD1
    rid_ctl_val = spoofed_rid  # Enable bit NOT set
    await usb_bfm.send_etherbone_write(REG_RID_CTL, rid_ctl_val)

    # Configure DMA for read
    dma_addr = 0x11110000
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, dma_addr)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, 32)
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, 0)

    # Trigger DMA read
    await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER)

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected TX TLP to be captured"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: req_id=0x{pkt.req_id:04X}")

    # Verify RID is NOT the spoofed value (should be device's native ID)
    assert pkt.req_id != spoofed_rid, \
        f"RID should be device ID, not spoofed value 0x{spoofed_rid:04X}"


@cocotb.test()
async def test_requester_id_various_values(dut):
    """
    Test various requester ID override values including edge cases.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    test_rids = [
        0x0000,  # All zeros
        0xFFFF,  # All ones
        0x0100,  # Bus 1, Device 0, Function 0
        0x1234,  # Arbitrary value
    ]

    for spoofed_rid in test_rids:
        dut._log.info(f"Testing RID override 0x{spoofed_rid:04X}")

        # Configure spoofed RID
        rid_ctl_val = (1 << 31) | spoofed_rid
        await usb_bfm.send_etherbone_write(REG_RID_CTL, rid_ctl_val)

        # Configure and trigger DMA read
        await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, 0x1000)
        await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
        await usb_bfm.send_etherbone_write(REG_DMA_LEN, 16)
        await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, 0)
        await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER)

        await ClockCycles(dut.sys_clk, 200)

        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

        assert packet_data is not None, f"Expected to capture TLP for RID 0x{spoofed_rid:04X}"

        pkt = parse_monitor_packet(packet_data)
        assert pkt.req_id == spoofed_rid, \
            f"Expected RID 0x{spoofed_rid:04X}, got 0x{pkt.req_id:04X}"
        dut._log.info(f"  RID 0x{spoofed_rid:04X} verified")

        # Inject completion to allow DMA to complete
        beats = TLPBuilder.completion(
            requester_id=spoofed_rid,  # Use spoofed RID as requester
            completer_id=0x0000,
            tag=pkt.tag,
            data_bytes=bytes([0x55] * 16),
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0)

        # Wait for DMA to complete before next iteration
        await wait_for_dma_idle(usb_bfm)


@cocotb.test()
async def test_requester_id_register_readback(dut):
    """
    Verify RID_CTL register can be written and read back correctly.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    test_values = [
        0x00000000,
        0x0000FFFF,
        0x80000000,  # Enable bit only
        0x8000BEEF,  # Enable + value
        0xFFFFFFFF,
    ]

    for val in test_values:
        await usb_bfm.send_etherbone_write(REG_RID_CTL, val)
        readback = await usb_bfm.send_etherbone_read(REG_RID_CTL)

        dut._log.info(f"Wrote 0x{val:08X}, read 0x{readback:08X}")
        assert readback == val, f"Readback mismatch: wrote 0x{val:08X}, got 0x{readback:08X}"
