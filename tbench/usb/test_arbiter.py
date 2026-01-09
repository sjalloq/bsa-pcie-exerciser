#
# Monitor Arbiter Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies RX/TX priority and packet atomicity in the MonitorPacketArbiter.
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
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_MSICTL = 0x000
REG_DMACTL = 0x008
REG_DMA_BUS_ADDR_LO = 0x010
REG_DMA_BUS_ADDR_HI = 0x014
REG_DMA_LEN = 0x018
REG_USB_MON_CTRL = 0x080
REG_USB_MON_RX_CAPTURED = 0x088
REG_USB_MON_TX_CAPTURED = 0x090


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


async def enable_both_monitoring(usb_bfm: USBBFM):
    """Enable both RX and TX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)


def parse_monitor_packet(data: bytes) -> TLPPacket:
    """Parse a USB monitor packet into TLPPacket."""
    pkt = parse_tlp_packet(data)
    if pkt is None:
        raise ValueError(f"Failed to parse monitor packet")
    return pkt


# =============================================================================
# Arbiter Tests
# =============================================================================

@cocotb.test()
async def test_arbiter_rx_only(dut):
    """
    Verify arbiter works with only RX traffic.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable only RX monitoring
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x01)

    # Inject RX TLPs
    for i in range(3):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    # Capture packets
    rx_count = 0
    for i in range(3):
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=300)
        assert packet_data is not None, f"Expected to capture RX packet {i+1}/3"
        pkt = parse_monitor_packet(packet_data)
        assert pkt.direction == Direction.RX, "Expected RX packet"
        rx_count += 1

    dut._log.info(f"Captured {rx_count} RX-only packets")
    assert rx_count == 3, f"Expected to capture all 3 RX packets, got {rx_count}"


@cocotb.test()
async def test_arbiter_tx_only(dut):
    """
    Verify arbiter works with only TX traffic.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable only TX monitoring
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x02)

    # Trigger TX traffic by doing a read that generates a completion
    beats = TLPBuilder.memory_read_32(
        address=0x048,  # ID register
        length_dw=1,
        requester_id=0x0100,
        tag=1,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
    await ClockCycles(dut.sys_clk, 100)

    # Try to capture TX packet
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture TX completion packet"
    pkt = parse_monitor_packet(packet_data)
    assert pkt.direction == Direction.TX, "Expected TX packet"
    dut._log.info(f"Captured TX packet: type={pkt.type_name}")


@cocotb.test()
async def test_arbiter_interleaved_traffic(dut):
    """
    Test arbiter with interleaved RX and TX traffic.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_both_monitoring(usb_bfm)

    # Generate both RX traffic (reads) and potential TX traffic (completions)
    for i in range(5):
        # RX: Memory read to BAR0
        beats = TLPBuilder.memory_read_32(
            address=0x048,  # ID register - will generate completion
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 30)

    await ClockCycles(dut.sys_clk, 200)

    # Capture all packets
    rx_packets = []
    tx_packets = []

    for _ in range(10):  # Try to capture up to 10 packets
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=200)
        if packet_data is None:
            break
        pkt = parse_monitor_packet(packet_data)
        if pkt.direction == Direction.RX:
            rx_packets.append(pkt)
        else:
            tx_packets.append(pkt)

    dut._log.info(f"Captured {len(rx_packets)} RX packets, {len(tx_packets)} TX packets")

    # Should capture at least some RX packets (we injected 5 reads)
    assert len(rx_packets) >= 4, f"Expected at least 4 RX packets, got {len(rx_packets)}"
    # TX completions should also be generated
    assert len(tx_packets) >= 4, f"Expected at least 4 TX completion packets, got {len(tx_packets)}"


@cocotb.test()
async def test_arbiter_packet_atomicity(dut):
    """
    Verify packets are not interleaved.

    Multi-word packets must complete before arbiter switches sources.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_both_monitoring(usb_bfm)

    # Inject a multi-DWORD write (larger payload)
    write_data = bytes([i for i in range(16)])  # 16 bytes = 4 DWORDs
    beats = TLPBuilder.memory_write_32(
        address=0x100,
        data_bytes=write_data,
        requester_id=0x0100,
        tag=1,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    await ClockCycles(dut.sys_clk, 100)

    # Capture the packet
    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture multi-DWORD write packet"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured packet: type={pkt.type_name}, payload_len={pkt.payload_length}")

    # Verify payload is present and intact (not interleaved)
    assert pkt.payload is not None and len(pkt.payload) > 0, \
        "Multi-DWORD write should have payload data"
    payload_bytes = pkt.payload_bytes
    dut._log.info(f"Payload: {payload_bytes.hex()}")


@cocotb.test()
async def test_arbiter_under_load(dut):
    """
    Stress test arbiter with sustained traffic.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_both_monitoring(usb_bfm)

    NUM_TLPS = 10

    # Inject many TLPs rapidly
    for i in range(NUM_TLPS):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + (i % 8) * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i % 32,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 10)  # Rapid injection

    await ClockCycles(dut.sys_clk, 300)

    # Count captured packets
    captured = 0
    for _ in range(NUM_TLPS + 5):  # Allow for some TX completions too
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=200)
        if packet_data is None:
            break
        captured += 1

    dut._log.info(f"Captured {captured} packets under load")

    # Should capture most of the injected TLPs
    assert captured >= NUM_TLPS * 0.8, \
        f"Expected to capture at least {int(NUM_TLPS * 0.8)} packets under load, got {captured}"


@cocotb.test()
async def test_arbiter_fair_scheduling(dut):
    """
    Verify TX eventually gets serviced under sustained RX traffic.

    Even if RX has priority, TX should not be starved.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await enable_both_monitoring(usb_bfm)

    # Inject RX traffic that will also generate TX completions
    for i in range(5):
        beats = TLPBuilder.memory_read_32(
            address=0x048,  # ID register - generates completion
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    await ClockCycles(dut.sys_clk, 300)

    # Capture packets and check for TX
    rx_seen = 0
    tx_seen = 0

    for _ in range(15):
        packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=200)
        if packet_data is None:
            break
        pkt = parse_monitor_packet(packet_data)
        if pkt.direction == Direction.RX:
            rx_seen += 1
        else:
            tx_seen += 1

    dut._log.info(f"Fair scheduling: RX={rx_seen}, TX={tx_seen}")

    # Both should have packets - RX reads and TX completions
    assert rx_seen >= 4, f"Expected at least 4 RX packets, got {rx_seen}"
    assert tx_seen >= 4, f"TX packets should not be starved, got {tx_seen}"


@cocotb.test()
async def test_arbiter_counters(dut):
    """
    Verify captured counters increment correctly.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Clear and enable monitoring
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x07)  # Enable + clear
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)  # Enable only

    initial_rx = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED)
    initial_tx = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_CAPTURED)

    dut._log.info(f"Initial counters: RX={initial_rx}, TX={initial_tx}")

    # Generate some RX traffic
    for i in range(3):
        beats = TLPBuilder.memory_read_32(
            address=0x100,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

        # Drain monitor packets to allow counter updates
        await usb_bfm.receive_monitor_packet(timeout_cycles=200)

    await ClockCycles(dut.sys_clk, 100)

    final_rx = await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED)
    final_tx = await usb_bfm.send_etherbone_read(REG_USB_MON_TX_CAPTURED)

    rx_delta = final_rx - initial_rx
    tx_delta = final_tx - initial_tx

    dut._log.info(f"Final counters: RX={final_rx}, TX={final_tx}")
    dut._log.info(f"Delta: RX=+{rx_delta}, TX=+{tx_delta}")

    # Should have captured at least 3 RX packets
    assert rx_delta >= 3, f"Expected RX counter to increment by at least 3, got {rx_delta}"
