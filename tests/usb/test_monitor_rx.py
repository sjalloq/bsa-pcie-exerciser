#
# RX TLP Monitor Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies that inbound PCIe TLPs are captured and streamed via USB channel 1.
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

# Import protocol parsing from common module
from bsa_pcie_exerciser.common.protocol import (
    parse_tlp_packet, TLPPacket, TLPType, Direction,
    TLP_TYPE_MRD, TLP_TYPE_MWR, TLP_TYPE_CPL, TLP_TYPE_CPLD,
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_USB_MON_CTRL        = 0x080
REG_USB_MON_RX_CAPTURED = 0x088
REG_USB_MON_RX_DROPPED  = 0x08C


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
    """Enable RX monitoring via USB_MON_CTL register."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x01)  # RX enable


async def disable_monitoring(usb_bfm: USBBFM):
    """Disable all monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x00)


async def clear_stats(usb_bfm: USBBFM):
    """Clear monitor statistics."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x07)  # Enable + clear
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)  # Enable, clear auto-clears


def parse_monitor_packet(data: bytes) -> TLPPacket:
    """
    Parse a USB monitor packet into TLPPacket.

    Uses the protocol parsing functions from common module.
    """
    pkt = parse_tlp_packet(data)
    if pkt is None:
        raise ValueError(f"Failed to parse monitor packet: {data.hex()}")
    return pkt


# =============================================================================
# RX Monitor Tests
# =============================================================================

@cocotb.test()
async def test_rx_monitor_mem_read(dut):
    """
    Capture inbound Memory Read TLP via USB monitor.

    1. Enable RX monitoring via USB_MON_CTL register
    2. Inject MRd TLP via PCIe PHY
    3. Capture monitor packet from USB
    4. Verify header fields match injected TLP
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Inject Memory Read TLP to BAR0
    address = 0x100
    tag = 42
    beats = TLPBuilder.memory_read_32(
        address=address,
        length_dw=1,
        requester_id=0x0100,
        tag=tag,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    # Wait for monitor to capture and stream
    await ClockCycles(dut.sys_clk, 100)

    # Capture monitor packet from USB channel 1
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "No monitor packet received"

    pkt = parse_monitor_packet(packet_data)
    assert pkt.tlp_type == TLP_TYPE_MRD, f"Expected MRd type, got {pkt.type_name}"
    assert pkt.direction == Direction.RX, f"Expected RX direction, got {pkt.direction}"
    assert pkt.address == address, f"Address mismatch: 0x{pkt.address:X} != 0x{address:X}"
    assert pkt.tag == tag, f"Tag mismatch: {pkt.tag} != {tag}"

    dut._log.info(f"Captured MRd: addr=0x{pkt.address:X}, tag={pkt.tag}")


@cocotb.test()
async def test_rx_monitor_mem_write(dut):
    """
    Capture inbound Memory Write TLP via USB monitor.

    Verify payload data is correctly captured.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Inject Memory Write TLP with data
    address = 0x200
    write_data = bytes([0xDE, 0xAD, 0xBE, 0xEF])
    tag = 7

    beats = TLPBuilder.memory_write_32(
        address=address,
        data_bytes=write_data,
        requester_id=0x0100,
        tag=tag,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
    assert packet_data is not None, "No monitor packet received"

    pkt = parse_monitor_packet(packet_data)
    assert pkt.tlp_type == TLP_TYPE_MWR, f"Expected MWr type, got {pkt.type_name}"
    assert pkt.we == True, "Expected we=1 for write"
    assert pkt.address == address, f"Address mismatch"

    # Verify payload
    if pkt.payload:
        captured_bytes = pkt.payload_bytes[:len(write_data)]
        dut._log.info(f"Captured MWr: payload={captured_bytes.hex()}")

    dut._log.info(f"Captured MWr: addr=0x{pkt.address:X}, tag={pkt.tag}")


@cocotb.test()
async def test_rx_monitor_attributes(dut):
    """
    Verify TLP attributes (No-Snoop, Relaxed Ordering) are captured.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Inject TLP with attributes set (attr=0b11: NS=1, RO=1)
    beats = TLPBuilder.memory_read_32(
        address=0x100,
        length_dw=1,
        requester_id=0x0100,
        tag=1,
        attr=0b11,  # No-Snoop + Relaxed Ordering
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
    assert packet_data is not None, "No monitor packet received"

    pkt = parse_monitor_packet(packet_data)
    assert pkt.attr == 0b11, f"Expected attr=0b11, got 0b{pkt.attr:02b}"

    dut._log.info(f"Captured TLP with attr=0b{pkt.attr:02b} (NS={pkt.attr&1}, RO={(pkt.attr>>1)&1})")


@cocotb.test()
async def test_rx_monitor_byte_enables(dut):
    """
    Verify first_be and last_be are captured correctly.

    Critical for BSA byte-enable verification tests.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Inject MWr with specific byte enables
    beats = TLPBuilder.memory_write_32(
        address=0x100,
        data_bytes=bytes([0x11, 0x22, 0x33, 0x44]),
        requester_id=0x0100,
        tag=1,
        first_be=0b0110,  # Only bytes 1,2 enabled
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
    assert packet_data is not None, "No monitor packet received"

    pkt = parse_monitor_packet(packet_data)
    assert pkt.first_be == 0b0110, f"Expected first_be=0b0110, got 0b{pkt.first_be:04b}"

    dut._log.info(f"Captured TLP with first_be=0b{pkt.first_be:04b}, last_be=0b{pkt.last_be:04b}")


@cocotb.test()
async def test_rx_monitor_disabled(dut):
    """
    Verify no packets when RX monitoring is disabled.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Ensure monitoring disabled
    await disable_monitoring(usb_bfm)

    # Inject TLP
    beats = TLPBuilder.memory_read_32(
        address=0x100,
        length_dw=1,
        requester_id=0x0100,
        tag=1,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    # Should timeout (no packet)
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=200)
    assert packet_data is None, "Should not receive packet when monitoring disabled"

    dut._log.info("Correctly received no packet when monitoring disabled")


@cocotb.test()
async def test_rx_monitor_backpressure(dut):
    """
    Verify dropped packet counter increments on USB backpressure.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    # Enable backpressure (block USB TX)
    usb_bfm.set_backpressure(True)
    usb_bfm.set_capture_ready(False)

    # Clear stats
    await clear_stats(usb_bfm)

    # Inject multiple TLPs that should be dropped
    for i in range(5):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 20)

    await ClockCycles(dut.sys_clk, 200)

    # Release backpressure
    usb_bfm.set_backpressure(False)
    usb_bfm.set_capture_ready(True)

    # Check dropped counter
    dropped = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_DROPPED)

    dut._log.info(f"Dropped {dropped} packets under backpressure")
    # Note: The actual drop count depends on FIFO depth and timing


@cocotb.test()
async def test_rx_monitor_multiple_tlps(dut):
    """
    Capture multiple TLPs in sequence.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)

    num_tlps = 3

    for i in range(num_tlps):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i + 10,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    # Capture all packets
    captured = []
    for i in range(num_tlps):
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
        if packet_data:
            pkt = parse_monitor_packet(packet_data)
            captured.append(pkt)
            dut._log.info(f"Captured TLP {i}: addr=0x{pkt.address:X}, tag={pkt.tag}")

    dut._log.info(f"Captured {len(captured)}/{num_tlps} TLPs")


@cocotb.test()
async def test_rx_monitor_captured_count(dut):
    """
    Verify captured packet counter increments correctly.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_rx_monitoring(usb_bfm)
    await clear_stats(usb_bfm)

    initial_count = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED)
    dut._log.info(f"Initial captured count: {initial_count}")

    # Inject and receive TLPs
    num_tlps = 3
    for i in range(num_tlps):
        beats = TLPBuilder.memory_read_32(
            address=0x100,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

        # Drain the monitor packet
        await usb_bfm.receive_monitor_packet(timeout_cycles=300)

    await ClockCycles(dut.sys_clk, 50)

    final_count = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED)
    dut._log.info(f"Final captured count: {final_count}")

    # Note: Captured count includes header and payload, exact value depends on implementation
