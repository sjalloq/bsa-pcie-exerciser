#
# USB Monitor Corner Case Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Tests targeting specific corner cases discovered during stress testing:
# - Header-only packets (no payload, e.g., MRd TLPs)
# - Single-beat packet handling
# - Truncation under payload FIFO backpressure
# - Width converter boundary conditions
# - FIFO near-full/near-empty behavior
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge
from cocotb.utils import get_sim_time

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tbench.common.usb_bfm import USBBFM
from tbench.common.pcie_bfm import PCIeBFM
from tbench.common.tlp_builder import TLPBuilder

from bsa_pcie_exerciser.common.protocol import (
    parse_tlp_packet, TLPPacket, TLPType, Direction,
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_ID = 0x048
REG_USB_MON_CTRL = 0x080
REG_USB_MON_RX_CAPTURED = 0x088
REG_USB_MON_RX_DROPPED = 0x08C


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


async def clear_and_enable(usb_bfm: USBBFM, rx=True, tx=True):
    """Clear stats and enable monitoring."""
    ctrl = (0x01 if rx else 0) | (0x02 if tx else 0) | 0x04  # + clear bit
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, ctrl)
    await ClockCycles(usb_bfm.clk, 5)
    ctrl = (0x01 if rx else 0) | (0x02 if tx else 0)
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, ctrl)


async def get_monitor_stats(usb_bfm: USBBFM) -> dict:
    """Read all monitor statistics."""
    return {
        'rx_captured': await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED),
        'rx_dropped': await usb_bfm.send_etherbone_read(REG_USB_MON_RX_DROPPED),
    }


async def drain_monitor_packets(usb_bfm: USBBFM, max_packets=1000,
                                timeout_per_packet=500, debug=False) -> list:
    """Drain all pending monitor packets."""
    packets = []
    while len(packets) < max_packets:
        data = await usb_bfm.receive_monitor_packet(timeout_cycles=timeout_per_packet, debug=debug)
        if data is None:
            break
        pkt = parse_tlp_packet(data)
        if pkt is None:
            raise AssertionError("Malformed monitor packet encountered during drain")
        packets.append(pkt)
    return packets


# =============================================================================
# CORNER CASE 1: Header-Only Packets (Memory Reads with no payload)
# =============================================================================

@cocotb.test()
async def test_header_only_mrd_single(dut):
    """
    Single Memory Read TLP (header-only, no payload).

    This tests the arbiter's header_last fix - header-only packets must
    assert last on the final header word.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Inject single MRd (header only, no payload)
    beats = TLPBuilder.memory_read_32(
        address=0x100,
        length_dw=1,
        requester_id=0x0100,
        tag=0x42,
    )
    await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

    # Should receive one packet
    packets = await drain_monitor_packets(usb_bfm, timeout_per_packet=500)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Header-only MRd: captured={stats['rx_captured']}, received={len(packets)}")

    assert stats['rx_captured'] == 1, "Should capture 1 packet"
    assert len(packets) >= 1, "Should receive 1 packet"
    if len(packets) >= 1:
        assert packets[0].tlp_type == TLPType.MRD, "Should be MRd"
        assert packets[0].tag == 0x42, f"Tag mismatch: {packets[0].tag}"


@cocotb.test()
async def test_header_only_mrd_burst(dut):
    """
    Burst of Memory Read TLPs (all header-only).

    Tests arbiter handling multiple header-only packets in sequence.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Inject 20 MRd TLPs with enough gap for FIFO to drain
    # Header FIFO is 4-deep, so need ~50 cycles per packet for USB to drain
    for i in range(20):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Header-only MRd burst: captured={stats['rx_captured']}, received={len(packets)}")

    assert stats['rx_captured'] == 20, f"Should capture 20, got {stats['rx_captured']}"
    assert len(packets) == 20, f"Should receive 20, got {len(packets)}"


@cocotb.test()
async def test_mixed_header_only_and_payload(dut):
    """
    Alternating MRd (header-only) and MWr (with payload).

    Tests arbiter correctly handles transitions between header-only
    and payload packets.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    mrd_count = 0
    mwr_count = 0

    for i in range(20):
        if i % 2 == 0:
            # MRd (header-only)
            beats = TLPBuilder.memory_read_32(
                address=0x100 + i * 4,
                length_dw=1,
                requester_id=0x0100,
                tag=i,
            )
            mrd_count += 1
        else:
            # MWr (with payload)
            beats = TLPBuilder.memory_write_32(
                address=0x200 + i * 4,
                data_bytes=bytes([i] * 8),
                requester_id=0x0100,
                tag=i,
            )
            mwr_count += 1

        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    received_mrd = sum(1 for p in packets if p.tlp_type == TLPType.MRD)
    received_mwr = sum(1 for p in packets if p.tlp_type == TLPType.MWR)

    dut._log.info(f"Mixed packets: MRd={received_mrd}/{mrd_count}, MWr={received_mwr}/{mwr_count}")

    assert stats['rx_captured'] == 20, f"Should capture 20, got {stats['rx_captured']}"
    assert received_mrd == mrd_count, f"MRd mismatch: {received_mrd}/{mrd_count}"
    assert received_mwr == mwr_count, f"MWr mismatch: {received_mwr}/{mwr_count}"


# =============================================================================
# CORNER CASE 2: Single-Beat Packet Drops
# =============================================================================

@cocotb.test()
async def test_single_beat_drop_counting(dut):
    """
    Single-beat packets dropped when header FIFO full.

    This tests the single_beat_drop fix - single-beat drops (first=last=1)
    must use combinatorial detection since dropping flag is registered.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Apply backpressure to cause drops
    usb_bfm.set_backpressure(True)

    # Inject single-beat MRd TLPs (first=last=1)
    for i in range(20):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 5)

    await ClockCycles(dut.sys_clk, 100)

    # Release backpressure
    usb_bfm.set_backpressure(False)
    await ClockCycles(dut.sys_clk, 500)

    stats = await get_monitor_stats(usb_bfm)
    packets = await drain_monitor_packets(usb_bfm)

    total = stats['rx_captured'] + stats['rx_dropped']
    dut._log.info(f"Single-beat drops: captured={stats['rx_captured']}, "
                  f"dropped={stats['rx_dropped']}, total={total}")

    # Key assertion: total should equal injected count (no double-counting)
    assert total == 20, f"Total should be 20, got {total} (double-counting bug)"


# =============================================================================
# CORNER CASE 3: FIFO Capacity Boundaries
# =============================================================================

@cocotb.test()
async def test_header_fifo_exact_capacity(dut):
    """
    Fill header FIFO to exact capacity (4 entries).

    Tests behavior at FIFO boundary - should capture 4, drop the rest.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Block USB to prevent draining
    usb_bfm.set_backpressure(True)

    # Header FIFO is 4 deep (256-bit entries)
    # Inject exactly 4 + a few more to test boundary
    for i in range(8):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 5)

    await ClockCycles(dut.sys_clk, 100)

    usb_bfm.set_backpressure(False)
    await ClockCycles(dut.sys_clk, 500)

    stats = await get_monitor_stats(usb_bfm)
    packets = await drain_monitor_packets(usb_bfm)

    dut._log.info(f"FIFO capacity test: captured={stats['rx_captured']}, "
                  f"dropped={stats['rx_dropped']}, received={len(packets)}")

    # Should have captured some and dropped others
    assert stats['rx_captured'] > 0, "Should capture some packets"
    assert stats['rx_captured'] + stats['rx_dropped'] == 8, "Total should be 8"


# =============================================================================
# CORNER CASE 4: Varying Payload Sizes
# =============================================================================

@cocotb.test()
async def test_payload_size_sweep(dut):
    """
    Sweep through various payload sizes (1 to 16 DWORDs).

    Tests width converter handling of different sizes.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Test sizes from 4 bytes (1 DW) to 64 bytes (16 DW)
    test_sizes = [4, 8, 12, 16, 20, 24, 28, 32, 48, 64]

    for size in test_sizes:
        payload = bytes([i & 0xFF for i in range(size)])
        beats = TLPBuilder.memory_write_32(
            address=0x200,
            data_bytes=payload,
            requester_id=0x0100,
            tag=size,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        # Larger packets need more USB drain time
        await ClockCycles(dut.sys_clk, 100)

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Payload size sweep: captured={stats['rx_captured']}, received={len(packets)}")

    assert len(packets) == len(test_sizes), f"Should receive {len(test_sizes)}, got {len(packets)}"

    # Verify payload lengths
    for pkt in packets:
        expected_dw = (pkt.tag + 3) // 4  # tag = size in bytes
        dut._log.info(f"Tag {pkt.tag}: payload_length={pkt.payload_length}, expected={expected_dw}")
        assert pkt.payload_length == expected_dw, \
            f"Payload length mismatch for size {pkt.tag}: got {pkt.payload_length}, expected {expected_dw}"


# =============================================================================
# CORNER CASE 5: Address Boundary Testing
# =============================================================================

@cocotb.test()
async def test_address_bar_boundaries(dut):
    """
    Test addresses at BAR boundaries.

    Addresses are masked to BAR-relative offsets by depacketizer.
    For 4KB BAR: 0x000-0xFFF are valid; addresses >= 0x1000 wrap.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Test addresses at various points within the 4KB BAR
    test_addresses = [
        0x000,   # Start of BAR
        0x004,   # Aligned
        0x100,   # Typical offset
        0x7FC,   # Near middle
        0xFFC,   # End of BAR (last aligned DWORD)
    ]

    for addr in test_addresses:
        beats = TLPBuilder.memory_read_32(
            address=addr,
            length_dw=1,
            requester_id=0x0100,
            tag=addr & 0xFF,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)

    dut._log.info(f"Address boundary test: received {len(packets)} packets")

    assert len(packets) == len(test_addresses), f"Should receive {len(test_addresses)}"

    # Verify addresses match (they should be preserved within BAR range)
    for pkt, expected_addr in zip(packets, test_addresses):
        dut._log.info(f"Expected 0x{expected_addr:04X}, got 0x{pkt.address:08X}")
        assert pkt.address == expected_addr, f"Address mismatch: 0x{pkt.address:08X} != 0x{expected_addr:04X}"


# =============================================================================
# CORNER CASE 6: Rapid Enable/Disable Toggle
# =============================================================================

@cocotb.test()
async def test_enable_disable_toggle(dut):
    """
    Rapidly toggle monitoring enable during traffic.

    Tests that enable/disable transitions don't cause corruption.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    total_injected = 0

    for cycle in range(5):
        # Enable
        await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x01)

        # Inject some packets with enough gap
        for i in range(5):
            beats = TLPBuilder.memory_read_32(
                address=0x100 + i * 4,
                length_dw=1,
                requester_id=0x0100,
                tag=cycle * 5 + i,
            )
            await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
            total_injected += 1
            await ClockCycles(dut.sys_clk, 30)

        # Disable
        await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x00)
        await ClockCycles(dut.sys_clk, 50)

    # Re-enable and drain
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x01)
    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Enable/disable toggle: injected={total_injected}, "
                  f"captured={stats['rx_captured']}, received={len(packets)}")

    # Should have captured some packets (not all, since disabled during some injections)
    assert stats['rx_captured'] > 0, "Should capture some packets"
    assert stats['rx_captured'] <= total_injected, "Can't capture more than injected"


# =============================================================================
# CORNER CASE 7: Back-to-Back Packets with Zero Gap
# =============================================================================

@cocotb.test()
async def test_back_to_back_zero_gap(dut):
    """
    Inject packets with zero gap between them.

    Tests that the capture engine handles immediate first->first transitions.
    With a 4-entry header FIFO, some drops are expected under zero-gap injection.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Inject 10 packets with no gap
    for i in range(10):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        # No extra delay - just the inject_tlp handshakes

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    total = stats['rx_captured'] + stats['rx_dropped']
    dut._log.info(f"Back-to-back: captured={stats['rx_captured']}, "
                  f"dropped={stats['rx_dropped']}, received={len(packets)}")

    # Total should equal injected (no double-counting or lost packets)
    assert total == 10, f"Total should be 10, got {total}"
    # Should capture at least the FIFO depth worth
    assert stats['rx_captured'] >= 4, f"Should capture at least 4 (FIFO depth)"


# =============================================================================
# CORNER CASE 8: TLP Attributes (NS, RO, AT)
# =============================================================================

@cocotb.test()
async def test_tlp_attributes_preserved(dut):
    """
    Verify TLP attributes (No-Snoop, Relaxed Ordering, AT) are captured.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Test with various attribute combinations
    attr_tests = [
        (0b00, 0b00),  # No attributes
        (0b01, 0b00),  # No-Snoop
        (0b10, 0b00),  # Relaxed Ordering
        (0b11, 0b00),  # Both NS and RO
        (0b00, 0b01),  # AT=1 (translation request)
        (0b11, 0b10),  # All attributes + AT=2 (translated)
    ]

    for attr, at in attr_tests:
        beats = TLPBuilder.memory_write_32(
            address=0x200,
            data_bytes=bytes([0xAB] * 4),
            requester_id=0x0100,
            tag=(attr << 2) | at,  # Encode attr/at in tag for verification
            attr=attr,
            at=at,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 50)

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)

    dut._log.info(f"Attribute test: received {len(packets)} packets")

    assert len(packets) == len(attr_tests), f"Should receive {len(attr_tests)}"

    # Verify attributes are preserved
    for pkt, (expected_attr, expected_at) in zip(packets, attr_tests):
        dut._log.info(f"Tag 0x{pkt.tag:02X}: attr={pkt.attr}, at={pkt.at}, "
                      f"expected attr={expected_attr}, at={expected_at}")
        assert pkt.attr == expected_attr, f"Attr mismatch"
        assert pkt.at == expected_at, f"AT mismatch"
