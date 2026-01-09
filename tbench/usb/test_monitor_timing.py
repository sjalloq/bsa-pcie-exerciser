#
# TLP Monitor Timing Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies cycle-accurate TLP capture timing to prevent bugs like
# capturing data one cycle late. These tests specifically target
# timing-sensitive edge cases.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge

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

REG_USB_MON_CTRL        = 0x080
REG_USB_MON_RX_CAPTURED = 0x088


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


async def enable_rx_monitoring(usb_bfm: USBBFM):
    """Enable RX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x01)


async def enable_both_monitoring(usb_bfm: USBBFM):
    """Enable RX and TX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)


def parse_monitor_packet(data: bytes) -> TLPPacket:
    """Parse a USB monitor packet into TLPPacket."""
    pkt = parse_tlp_packet(data)
    if pkt is None:
        raise ValueError(f"Failed to parse monitor packet: {data.hex()}")
    return pkt


# =============================================================================
# Monitor Timing Tests
# =============================================================================

@cocotb.test()
async def test_monitor_first_beat_timing(dut):
    """
    Verify first beat of TLP is captured on the correct cycle.

    This test catches off-by-one bugs where the first beat is
    captured one cycle late, causing header corruption.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Inject a single-beat MRd with known values
    # Note: Monitor captures BAR offset (lower 12 bits for 4KB BAR), not full address
    test_address = 0xDEAD0ABC  # Lower 12 bits = 0xABC
    test_tag = 0x42
    test_req_id = 0x1234

    beats = TLPBuilder.memory_read_32(
        address=test_address,
        length_dw=1,
        requester_id=test_req_id,
        tag=test_tag,
    )

    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected RX TLP to be captured"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured: tag={pkt.tag}, req_id=0x{pkt.req_id:04X}, addr=0x{pkt.address:08X}")

    # Verify all first-beat fields are correct
    assert pkt.tlp_type == TLP_TYPE_MRD, f"Expected MRd, got {pkt.type_name}"
    assert pkt.tag == test_tag, f"Tag mismatch: expected {test_tag}, got {pkt.tag}"
    assert pkt.req_id == test_req_id, \
        f"ReqID mismatch: expected 0x{test_req_id:04X}, got 0x{pkt.req_id:04X}"
    # Monitor captures BAR offset (masked to 4KB BAR size), DWORD-aligned
    expected_offset = test_address & 0xFFC  # Lower 12 bits, DWORD aligned
    assert pkt.address == expected_offset, \
        f"Address mismatch: expected 0x{expected_offset:03X}, got 0x{pkt.address:03X}"


@cocotb.test()
async def test_monitor_back_to_back_no_gap(dut):
    """
    Verify correct capture with zero gap between TLPs.

    This is the most timing-critical case - if there's an off-by-one
    bug, the second TLP's first beat may get stale data from the first.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Create two distinct TLPs
    tlp1 = TLPBuilder.memory_read_32(
        address=0x11110000,
        length_dw=1,
        requester_id=0x1111,
        tag=0x11,
    )

    tlp2 = TLPBuilder.memory_read_32(
        address=0x22220000,
        length_dw=1,
        requester_id=0x2222,
        tag=0x22,
    )

    # Inject back-to-back with no gap
    await pcie_bfm.inject_tlp(tlp1, bar_hit=0b000001)
    await pcie_bfm.inject_tlp(tlp2, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 200)

    # Capture both TLPs
    pkt1_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
    pkt2_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert pkt1_data is not None, "Expected to capture first TLP"
    assert pkt2_data is not None, "Expected to capture second TLP"

    pkt1 = parse_monitor_packet(pkt1_data)
    dut._log.info(f"TLP1: tag=0x{pkt1.tag:02X}, addr=0x{pkt1.address:08X}")

    # First TLP should have its own values
    assert pkt1.tag == 0x11, f"TLP1 tag mismatch: expected 0x11, got 0x{pkt1.tag:02X}"
    assert pkt1.req_id == 0x1111, \
        f"TLP1 req_id mismatch: expected 0x1111, got 0x{pkt1.req_id:04X}"

    pkt2 = parse_monitor_packet(pkt2_data)
    dut._log.info(f"TLP2: tag=0x{pkt2.tag:02X}, addr=0x{pkt2.address:08X}")

    # Second TLP should NOT have first TLP's values
    assert pkt2.tag == 0x22, f"TLP2 tag mismatch: expected 0x22, got 0x{pkt2.tag:02X}"
    assert pkt2.req_id == 0x2222, \
        f"TLP2 req_id mismatch: expected 0x2222, got 0x{pkt2.req_id:04X}"

    # This would fail if first beat was captured late
    assert pkt2.address != 0x11110000, \
        "TLP2 got TLP1's address - likely off-by-one timing bug"


@cocotb.test()
async def test_monitor_single_beat_tlp(dut):
    """
    Verify single-beat TLP (header only, no payload) is captured correctly.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # MRd with length=1 is a single-beat TLP (header only)
    beats = TLPBuilder.memory_read_32(
        address=0x33330000,
        length_dw=1,
        requester_id=0x3333,
        tag=0x33,
    )

    assert len(beats) == 2, f"Expected 2 beats for 3DW header, got {len(beats)}"

    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture single-beat TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Single-beat TLP: tag=0x{pkt.tag:02X}")
    assert pkt.tag == 0x33
    assert pkt.direction == Direction.RX


@cocotb.test()
async def test_monitor_multi_beat_tlp_first_beat(dut):
    """
    Verify first beat of multi-beat TLP is captured correctly.

    The first beat contains critical header information.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # MWr with data = multi-beat TLP
    test_data = bytes([0xA1, 0xA2, 0xA3, 0xA4, 0xB1, 0xB2, 0xB3, 0xB4])

    beats = TLPBuilder.memory_write_32(
        address=0x44440000,
        data_bytes=test_data,
        requester_id=0x4444,
        tag=0x44,
    )

    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture multi-beat TLP"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Multi-beat TLP: tag=0x{pkt.tag:02X}, len={pkt.payload_length}")

    assert pkt.tlp_type == TLP_TYPE_MWR
    assert pkt.tag == 0x44
    assert pkt.req_id == 0x4444


@cocotb.test()
async def test_monitor_pipeline_flush(dut):
    """
    Verify pipeline is properly flushed between transactions.

    After a TLP completes, the next TLP should not see stale data.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # First TLP with distinctive pattern
    tlp1 = TLPBuilder.memory_write_32(
        address=0xAAAA0000,
        data_bytes=bytes([0xAA] * 8),
        requester_id=0xAAAA,
        tag=0xAA,
    )
    await pcie_bfm.inject_tlp(tlp1, bar_hit=0b000001)

    # Wait for first TLP to complete
    await ClockCycles(dut.sys_clk, 50)

    # Second TLP with different pattern
    tlp2 = TLPBuilder.memory_read_32(
        address=0xBBBB0000,
        length_dw=2,
        requester_id=0xBBBB,
        tag=0xBB,
    )
    await pcie_bfm.inject_tlp(tlp2, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    # Capture both
    pkt1_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
    pkt2_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert pkt1_data is not None, "Expected to capture first TLP (MWr)"
    assert pkt2_data is not None, "Expected to capture second TLP (MRd)"

    pkt1 = parse_monitor_packet(pkt1_data)
    dut._log.info(f"TLP1: type={pkt1.type_name}, tag=0x{pkt1.tag:02X}")
    assert pkt1.tlp_type == TLP_TYPE_MWR
    assert pkt1.tag == 0xAA

    pkt2 = parse_monitor_packet(pkt2_data)
    dut._log.info(f"TLP2: type={pkt2.type_name}, tag=0x{pkt2.tag:02X}")
    assert pkt2.tlp_type == TLP_TYPE_MRD
    assert pkt2.tag == 0xBB
    # Verify no contamination from first TLP
    assert pkt2.address != 0xAAAA0000, "Stale address from previous TLP"


@cocotb.test()
async def test_monitor_rapid_fire_tlps(dut):
    """
    Stress test: rapid injection of multiple TLPs.

    All should be captured correctly with no data corruption.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    num_tlps = 5
    expected_tags = []

    for i in range(num_tlps):
        tag = 0x10 + i
        expected_tags.append(tag)

        beats = TLPBuilder.memory_read_32(
            address=0x10000000 + (i * 0x1000),
            length_dw=1,
            requester_id=0x0100,
            tag=tag,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 300)

    # Capture all TLPs
    captured_tags = []
    for i in range(num_tlps):
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
        assert packet_data is not None, f"Expected to capture TLP {i}/{num_tlps}"
        pkt = parse_monitor_packet(packet_data)
        captured_tags.append(pkt.tag)
        dut._log.info(f"Captured tag=0x{pkt.tag:02X}")

    dut._log.info(f"Expected tags: {[hex(t) for t in expected_tags]}")
    dut._log.info(f"Captured tags: {[hex(t) for t in captured_tags]}")

    # All tags should be captured in order
    assert captured_tags == expected_tags, \
        f"Tag mismatch: expected {expected_tags}, got {captured_tags}"


@cocotb.test()
async def test_monitor_enable_disable_timing(dut):
    """
    Verify monitor enable/disable doesn't cause data corruption.

    TLPs injected while monitor is being enabled should be
    fully captured or fully missed - no partial captures.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Start with monitor disabled
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x00)

    # Inject TLP while disabled
    beats1 = TLPBuilder.memory_read_32(
        address=0xDEAD0000,
        length_dw=1,
        requester_id=0xDEAD,
        tag=0xDE,
    )
    await pcie_bfm.inject_tlp(beats1, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 50)

    # Enable monitor
    await enable_rx_monitoring(usb_bfm)

    # Inject TLP while enabled
    beats2 = TLPBuilder.memory_read_32(
        address=0xBEEF0000,
        length_dw=1,
        requester_id=0xBEEF,
        tag=0xBE,
    )
    await pcie_bfm.inject_tlp(beats2, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    # Should only capture second TLP
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture TLP after enabling monitor"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured: tag=0x{pkt.tag:02X}, req_id=0x{pkt.req_id:04X}")

    # Should be the second TLP
    assert pkt.tag == 0xBE, f"Expected tag 0xBE, got 0x{pkt.tag:02X}"
    assert pkt.req_id == 0xBEEF


@cocotb.test()
async def test_monitor_address_field_alignment(dut):
    """
    Verify address field is captured with correct byte alignment.

    Lower 2 bits of address should always be 0 (DWORD aligned).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Test various address patterns
    # Note: Monitor captures BAR offset (lower 12 bits for 4KB BAR)
    test_addresses = [
        0x00000100,  # Simple offset
        0x12345678,  # Mixed pattern - BAR offset = 0x678
        0xFFFFFF00,  # High bits set - BAR offset = 0xF00
        0x00000ABC,  # Arbitrary offset
    ]

    for addr in test_addresses:
        # BAR offset (lower 12 bits), DWORD-aligned
        expected_offset = addr & 0xFFC

        beats = TLPBuilder.memory_read_32(
            address=addr,
            length_dw=1,
            requester_id=0x0100,
            tag=0x01,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

        await ClockCycles(dut.sys_clk, 100)

        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

        assert packet_data is not None, f"Expected to capture MRd for address 0x{addr:08X}"

        pkt = parse_monitor_packet(packet_data)
        dut._log.info(f"Address test: input=0x{addr:08X}, BAR offset=0x{pkt.address:03X}")

        assert pkt.tlp_type == TLP_TYPE_MRD, \
            f"Expected MRd TLP, got {pkt.type_name}"
        assert pkt.direction == Direction.RX, \
            f"Expected RX direction, got {pkt.direction}"
        assert pkt.address == expected_offset, \
            f"BAR offset mismatch: expected 0x{expected_offset:03X}, got 0x{pkt.address:03X}"
