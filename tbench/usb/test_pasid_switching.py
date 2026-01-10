#
# PASID Switching Tests (BSA e035-e036)
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies PASID context handling:
# - Switching PASID values between DMA operations
# - ATC entry isolation by PASID
# - Multiple concurrent PASID contexts
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tbench.common.usb_bfm import USBBFM
from tbench.common.pcie_bfm import PCIeBFM
from tbench.common.tlp_builder import TLPBuilder, ATS_PERM_RW

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
REG_PASID_VAL           = 0x020
REG_ATSCTL              = 0x024
REG_USB_MON_CTRL        = 0x080

# DMACTL bit definitions (per ARM BSA Exerciser spec)
DMACTL_TRIGGER    = (1 << 0)   # [3:0] trigger
DMACTL_DIRECTION  = (1 << 4)   # [4] 0=read, 1=write
DMACTL_PASID_EN   = (1 << 6)   # [6] PASID enable
DMACTL_PRIVILEGED = (1 << 7)   # [7] privileged
DMACTL_EXECUTE    = (1 << 8)   # [8] instruction/execute

# ATSCTL bit definitions
ATSCTL_TRIGGER      = (1 << 0)
ATSCTL_PASID_EN     = (1 << 3)
ATSCTL_CLEAR_ATC    = (1 << 5)
ATSCTL_SUCCESS      = (1 << 7)

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
    """Enable TX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x02)


async def configure_dma_with_pasid(usb_bfm: USBBFM, address: int, length: int,
                                    pasid: int, privileged: bool = False):
    """Configure DMA with PASID enabled."""
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, address & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, (address >> 32) & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, length)
    await usb_bfm.send_etherbone_write(REG_DMA_OFFSET, 0)
    await usb_bfm.send_etherbone_write(REG_PASID_VAL, pasid)


async def trigger_dma_read_with_pasid(usb_bfm: USBBFM, privileged: bool = False):
    """Trigger DMA read with PASID enabled."""
    ctl = DMACTL_TRIGGER | DMACTL_PASID_EN
    if privileged:
        ctl |= DMACTL_PRIVILEGED
    await usb_bfm.send_etherbone_write(REG_DMACTL, ctl)


async def trigger_dma_write_with_pasid(usb_bfm: USBBFM, privileged: bool = False):
    """Trigger DMA write with PASID enabled."""
    ctl = DMACTL_TRIGGER | DMACTL_DIRECTION | DMACTL_PASID_EN
    if privileged:
        ctl |= DMACTL_PRIVILEGED
    await usb_bfm.send_etherbone_write(REG_DMACTL, ctl)


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
# PASID Switching Tests
# =============================================================================

@cocotb.test()
async def test_pasid_value_in_dma_tlp(dut):
    """
    Verify DMA TLP contains correct PASID prefix when enabled.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Configure DMA with specific PASID
    test_pasid = 0x12345
    await configure_dma_with_pasid(usb_bfm, 0x10000000, 32, test_pasid)
    await trigger_dma_write_with_pasid(usb_bfm)

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA TLP with PASID"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TLP: pasid_valid={pkt.pasid_valid}, pasid=0x{pkt.pasid:05X}")

    assert pkt.pasid_valid, "Expected PASID prefix in TLP"
    assert pkt.pasid == test_pasid, \
        f"Expected PASID 0x{test_pasid:05X}, got 0x{pkt.pasid:05X}"


@cocotb.test()
async def test_pasid_switch_between_dma(dut):
    """
    Verify PASID value can be changed between DMA operations.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    pasid_values = [0x00001, 0x12345, 0xFFFFF]

    for pasid in pasid_values:
        dut._log.info(f"Testing PASID 0x{pasid:05X}")

        await configure_dma_with_pasid(usb_bfm, 0x20000000, 16, pasid)
        await trigger_dma_write_with_pasid(usb_bfm)

        await ClockCycles(dut.sys_clk, 200)

        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

        assert packet_data is not None, f"Expected to capture DMA TLP for PASID 0x{pasid:05X}"

        pkt = parse_monitor_packet(packet_data)

        assert pkt.pasid_valid, f"Expected PASID prefix for 0x{pasid:05X}"
        assert pkt.pasid == pasid, \
            f"PASID mismatch: expected 0x{pasid:05X}, got 0x{pkt.pasid:05X}"

        dut._log.info(f"  PASID 0x{pasid:05X} verified")

        await wait_for_dma_idle(usb_bfm)


@cocotb.test()
async def test_pasid_privileged_mode(dut):
    """
    Verify privileged mode bit (PMR) is set correctly in PASID prefix.

    The privileged bit is captured in header word 3 bit [29] for TX requests.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Test with privileged=False
    await configure_dma_with_pasid(usb_bfm, 0x30000000, 16, 0x100)
    await trigger_dma_write_with_pasid(usb_bfm, privileged=False)

    await ClockCycles(dut.sys_clk, 200)
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture non-privileged DMA TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Non-privileged: pasid_valid={pkt.pasid_valid}, pasid=0x{pkt.pasid:05X}, privileged={pkt.privileged}")
    assert pkt.pasid_valid, "Expected PASID prefix for non-privileged mode"
    assert not pkt.privileged, "Expected privileged=False when not set"

    await wait_for_dma_idle(usb_bfm)

    # Test with privileged=True
    await configure_dma_with_pasid(usb_bfm, 0x40000000, 16, 0x100)
    await trigger_dma_write_with_pasid(usb_bfm, privileged=True)

    await ClockCycles(dut.sys_clk, 200)
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture privileged DMA TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Privileged: pasid_valid={pkt.pasid_valid}, pasid=0x{pkt.pasid:05X}, privileged={pkt.privileged}")
    assert pkt.pasid_valid, "Expected PASID prefix for privileged mode"
    assert pkt.privileged, "Expected privileged=True when set"


@cocotb.test()
async def test_dma_without_pasid(dut):
    """
    Verify DMA without PASID enabled doesn't include PASID prefix.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Set PASID value but DON'T enable PASID in DMACTL
    await usb_bfm.send_etherbone_write(REG_PASID_VAL, 0xABCDE)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, 0x50000000)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_DMA_LEN, 16)

    # Trigger WITHOUT PASID_EN
    await usb_bfm.send_etherbone_write(REG_DMACTL, DMACTL_TRIGGER | DMACTL_DIRECTION)

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"PASID disabled: pasid_valid={pkt.pasid_valid}")
    assert not pkt.pasid_valid, "Should NOT have PASID prefix when disabled"


@cocotb.test()
async def test_multiple_pasid_contexts(dut):
    """
    Test multiple DMA operations with different PASID contexts.

    Simulates multiple process contexts using the exerciser.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    # Simulate 4 different process contexts
    contexts = [
        {"pasid": 0x0001, "addr": 0x10000000, "name": "Context A"},
        {"pasid": 0x0002, "addr": 0x20000000, "name": "Context B"},
        {"pasid": 0x0003, "addr": 0x30000000, "name": "Context C"},
        {"pasid": 0x0004, "addr": 0x40000000, "name": "Context D"},
    ]

    for ctx in contexts:
        dut._log.info(f"Testing {ctx['name']}: PASID=0x{ctx['pasid']:05X}")

        await configure_dma_with_pasid(usb_bfm, ctx['addr'], 8, ctx['pasid'])
        await trigger_dma_read_with_pasid(usb_bfm)

        await ClockCycles(dut.sys_clk, 100)

        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

        assert packet_data is not None, f"{ctx['name']}: Expected to capture DMA TLP"

        pkt = parse_monitor_packet(packet_data)

        assert pkt.pasid_valid, f"{ctx['name']}: Expected PASID prefix"
        assert pkt.pasid == ctx['pasid'], \
            f"{ctx['name']}: PASID mismatch"

        # Inject completion
        beats = TLPBuilder.completion(
            requester_id=0x0100,
            completer_id=0x0000,
            tag=pkt.tag,
            data_bytes=bytes([0x55] * 8),
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0)

        await wait_for_dma_idle(usb_bfm)

    dut._log.info("All PASID contexts verified successfully")


@cocotb.test()
async def test_pasid_max_value(dut):
    """
    Test maximum PASID value (20 bits = 0xFFFFF).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    max_pasid = 0xFFFFF

    await configure_dma_with_pasid(usb_bfm, 0x60000000, 16, max_pasid)
    await trigger_dma_write_with_pasid(usb_bfm)

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA TLP with max PASID"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Max PASID test: got 0x{pkt.pasid:05X}")
    assert pkt.pasid_valid, "Expected PASID prefix"
    assert pkt.pasid == max_pasid, \
        f"Expected max PASID 0x{max_pasid:05X}, got 0x{pkt.pasid:05X}"


@cocotb.test()
async def test_pasid_zero_value(dut):
    """
    Test PASID value of zero (valid but often special).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    await enable_tx_monitoring(usb_bfm)

    await configure_dma_with_pasid(usb_bfm, 0x70000000, 16, 0x00000)
    await trigger_dma_write_with_pasid(usb_bfm)

    await ClockCycles(dut.sys_clk, 200)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture DMA TLP with zero PASID"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Zero PASID test: valid={pkt.pasid_valid}, pasid=0x{pkt.pasid:05X}")
    assert pkt.pasid_valid, "PASID prefix should be present even for value 0"
    assert pkt.pasid == 0, "PASID should be 0"
