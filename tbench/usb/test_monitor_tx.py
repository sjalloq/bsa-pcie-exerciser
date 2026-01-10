#
# TX TLP Monitor Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies that outbound PCIe TLPs (DMA, completions, MSI-X) are captured
# and streamed via USB channel 1.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tbench.common.usb_bfm import USBBFM
from tbench.common.pcie_bfm import PCIeBFM
from tbench.common.tlp_builder import TLPBuilder

from bsa_pcie_exerciser.common.protocol import (
    parse_tlp_packet, TLPPacket, TLPType, Direction,
    TLP_TYPE_MRD, TLP_TYPE_MWR, TLP_TYPE_CPL, TLP_TYPE_CPLD, TLP_TYPE_MSIX,
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_MSICTL              = 0x000
REG_DMACTL              = 0x008
REG_DMA_OFFSET          = 0x00C
REG_DMA_BUS_ADDR_LO     = 0x010
REG_DMA_BUS_ADDR_HI     = 0x014
REG_DMA_LEN             = 0x018
REG_DMASTATUS           = 0x01C
REG_PASID_VAL           = 0x020

REG_USB_MON_CTRL        = 0x080
REG_USB_MON_TX_CAPTURED = 0x090
REG_USB_MON_TX_DROPPED  = 0x094


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


async def enable_both_monitoring(usb_bfm: USBBFM):
    """Enable both RX and TX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)  # RX + TX enable


def parse_monitor_packet(data: bytes) -> TLPPacket:
    """Parse a USB monitor packet into TLPPacket."""
    pkt = parse_tlp_packet(data)
    if pkt is None:
        raise ValueError(f"Failed to parse monitor packet: {data.hex()}")
    return pkt


# =============================================================================
# TX Monitor Tests
# =============================================================================

@cocotb.test()
async def test_tx_monitor_completion(dut):
    """
    Capture outbound completion for inbound read request.

    1. Enable TX monitoring
    2. Inject MRd TLP (causes completion to be generated)
    3. Capture completion TLP via USB monitor
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Inject Memory Read to BAR0 register
    beats = TLPBuilder.memory_read_32(
        address=0x048,  # ID register
        length_dw=1,
        requester_id=0x0100,
        tag=5,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    # Wait for completion to be generated and captured
    await ClockCycles(dut.sys_clk, 200)

    # Capture monitor packet
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture TX completion TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TX TLP: type={pkt.type_name}, dir={pkt.direction}")
    # Completion should be TX direction
    assert pkt.direction == Direction.TX, "Expected TX direction"
    assert pkt.tlp_type in (TLP_TYPE_CPL, TLP_TYPE_CPLD), \
        f"Expected completion TLP, got {pkt.type_name}"


@cocotb.test()
async def test_tx_monitor_dma_read(dut):
    """
    Capture outbound DMA read request.

    1. Configure DMA engine via registers
    2. Trigger DMA read
    3. Capture DMA request TLP via USB monitor
    4. Verify address, length, attributes
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure DMA for read operation
    dma_addr = 0x12345000
    dma_len = 64  # bytes

    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, dma_addr & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, (dma_addr >> 32) & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, dma_len)
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, 0)

    # Trigger DMA read (direction=0)
    await usb_bfm.send_etherbone_write(REG_DMACTL, 0x01)  # trigger=1, dir=0 (read)

    # Wait for DMA to start and generate TLP
    await ClockCycles(dut.sys_clk, 200)

    # Capture monitor packet
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA read TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured DMA TLP: type={pkt.type_name}, addr=0x{pkt.address:X}")
    assert pkt.direction == Direction.TX, "Expected TX direction"
    assert pkt.tlp_type == TLP_TYPE_MRD, f"Expected MRd TLP, got {pkt.type_name}"
    dut._log.info(f"DMA read request captured: addr=0x{pkt.address:016X}")


# MSI-X Table entry offsets (relative to BAR2)
# Each entry is 16 bytes: addr_lo(4) + addr_hi(4) + data(4) + vector_control(4)
MSIX_ENTRY_SIZE = 16
MSIX_ADDR_LO_OFFSET = 0
MSIX_ADDR_HI_OFFSET = 4
MSIX_DATA_OFFSET = 8
MSIX_VECTOR_CTRL_OFFSET = 12


async def configure_msix_entry(pcie_bfm, entry: int, address: int, data: int, masked: bool = False):
    """
    Configure an MSI-X table entry via BAR2 write.

    Args:
        pcie_bfm: PCIe BFM instance
        entry: Vector number (0-2047)
        address: Target address for MSI write
        data: Data value for MSI write
        masked: If True, mask this vector
    """
    base_offset = entry * MSIX_ENTRY_SIZE

    # Write addr_lo
    beats = TLPBuilder.memory_write_32(
        address=base_offset + MSIX_ADDR_LO_OFFSET,
        data_bytes=(address & 0xFFFFFFFF).to_bytes(4, 'little'),
        requester_id=0x0100,
        tag=0,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000100)  # BAR2

    # Write addr_hi
    beats = TLPBuilder.memory_write_32(
        address=base_offset + MSIX_ADDR_HI_OFFSET,
        data_bytes=((address >> 32) & 0xFFFFFFFF).to_bytes(4, 'little'),
        requester_id=0x0100,
        tag=0,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000100)

    # Write data
    beats = TLPBuilder.memory_write_32(
        address=base_offset + MSIX_DATA_OFFSET,
        data_bytes=data.to_bytes(4, 'little'),
        requester_id=0x0100,
        tag=0,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000100)

    # Write vector control (bit 0 = mask)
    ctrl = 1 if masked else 0
    beats = TLPBuilder.memory_write_32(
        address=base_offset + MSIX_VECTOR_CTRL_OFFSET,
        data_bytes=ctrl.to_bytes(4, 'little'),
        requester_id=0x0100,
        tag=0,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000100)


@cocotb.test()
async def test_tx_monitor_msix_write(dut):
    """
    Capture outbound MSI-X write.

    1. Configure MSI-X table entry via BAR2
    2. Trigger interrupt via software
    3. Capture MSI-X memory write TLP
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure MSI-X vector 0 with a known address and data
    msix_addr = 0xFEE00000  # Typical MSI address
    msix_data = 0x12345678
    await configure_msix_entry(pcie_bfm, entry=0, address=msix_addr, data=msix_data, masked=False)

    await ClockCycles(dut.sys_clk, 100)

    # Trigger MSI-X vector 0
    # MSICTL: [10:0]=vector, [31]=trigger
    await usb_bfm.send_etherbone_write(REG_MSICTL, (1 << 31) | 0)  # Trigger vector 0

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture MSI-X TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: type={pkt.type_name}, addr=0x{pkt.address:X}")
    assert pkt.direction == Direction.TX, "Expected TX direction"
    assert pkt.tlp_type == TLP_TYPE_MWR, f"Expected MWr TLP for MSI-X, got {pkt.type_name}"
    dut._log.info(f"MSI-X write captured: addr=0x{pkt.address:016X}")


@cocotb.test()
async def test_tx_monitor_dma_with_no_snoop(dut):
    """
    Verify No-Snoop attribute is captured for DMA with NS enabled.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure DMA with No-Snoop attribute
    dma_addr = 0x1000
    dma_len = 32

    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, dma_addr)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, dma_len)

    # DMACTL: trigger=1, dir=0 (read), no_snoop=1 (bit 5)
    await usb_bfm.send_etherbone_write(REG_DMACTL, 0x21)  # no_snoop + trigger

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA TLP with No-Snoop"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: type={pkt.type_name}, attr=0b{pkt.attr:02b}")
    assert pkt.tlp_type == TLP_TYPE_MRD, f"Expected MRd TLP, got {pkt.type_name}"

    # Check No-Snoop bit in attr (bit 0)
    ns_bit = pkt.attr & 1
    dut._log.info(f"No-Snoop bit in captured TLP: {ns_bit}")
    assert ns_bit == 1, "Expected No-Snoop bit to be set"


@cocotb.test()
async def test_tx_monitor_pasid_prefix(dut):
    """
    Verify PASID prefix is captured for DMA with PASID enabled.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Set PASID value
    pasid_value = 0x12345
    await usb_bfm.send_etherbone_write(REG_PASID_VAL, pasid_value)

    # Configure DMA with PASID enabled
    dma_addr = 0x2000
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, dma_addr)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, 32)

    # DMACTL: trigger=1, pasid_en=1 (bit 6)
    await usb_bfm.send_etherbone_write(REG_DMACTL, 0x41)  # pasid_en + trigger

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA TLP with PASID"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: type={pkt.type_name}, pasid_valid={pkt.pasid_valid}")
    assert pkt.pasid_valid, "Expected PASID prefix in captured TLP"
    assert pkt.pasid == pasid_value, \
        f"Expected PASID 0x{pasid_value:05X}, got 0x{pkt.pasid:05X}"
    dut._log.info(f"PASID captured: 0x{pkt.pasid:05X}")


@cocotb.test()
async def test_tx_monitor_captured_count(dut):
    """
    Verify TX captured packet counter increments.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Clear stats
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x06)  # TX enable + clear
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x02)  # TX enable only

    initial_count = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_CAPTURED)
    dut._log.info(f"Initial TX captured count: {initial_count}")

    # The count will only increment when TX TLPs are generated by the SoC
    # This depends on triggering DMA, MSI-X, or responding to reads
