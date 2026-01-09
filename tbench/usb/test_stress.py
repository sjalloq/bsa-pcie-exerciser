#
# USB Monitor Stress Tests
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Stress tests for the USB monitor subsystem targeting:
# - FIFO overflow/underflow conditions
# - Arbiter contention between RX/TX
# - Etherbone + Monitor crossbar contention
# - Backpressure handling
# - Long-running stability
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer, RisingEdge, Combine, First
from cocotb.utils import get_sim_time
import random

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.usb_bfm import USBBFM
from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder

from bsa_pcie_exerciser.common.protocol import (
    parse_tlp_packet, TLPPacket, TLPType, Direction,
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_MSICTL = 0x000
REG_DMACTL = 0x008
REG_DMA_BUS_ADDR_LO = 0x010
REG_DMA_BUS_ADDR_HI = 0x014
REG_DMA_LEN = 0x018
REG_ID = 0x048
REG_USB_MON_CTRL = 0x080
REG_USB_MON_RX_CAPTURED = 0x088
REG_USB_MON_RX_DROPPED = 0x08C
REG_USB_MON_TX_CAPTURED = 0x090
REG_USB_MON_TX_DROPPED = 0x094
REG_USB_MON_RX_TRUNCATED = 0x098
REG_USB_MON_TX_TRUNCATED = 0x09C


# =============================================================================
# Test Configuration
# =============================================================================

# Adjust these for longer/shorter stress runs
STRESS_PACKET_COUNT = 100
LONG_STRESS_PACKET_COUNT = 500
STABILITY_DURATION_CYCLES = 50000


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


async def enable_monitoring(usb_bfm: USBBFM, rx=True, tx=True):
    """Enable RX and/or TX monitoring."""
    ctrl = (0x01 if rx else 0) | (0x02 if tx else 0)
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, ctrl)


async def clear_and_enable(usb_bfm: USBBFM, rx=True, tx=True):
    """Clear stats and enable monitoring."""
    ctrl = (0x01 if rx else 0) | (0x02 if tx else 0) | 0x04  # + clear bit
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, ctrl)
    await ClockCycles(usb_bfm.clk, 5)
    # Clear bit auto-clears, but write again without it
    ctrl = (0x01 if rx else 0) | (0x02 if tx else 0)
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, ctrl)


async def get_monitor_stats(usb_bfm: USBBFM) -> dict:
    """Read all monitor statistics."""
    return {
        'rx_captured': await usb_bfm.send_etherbone_read(REG_USB_MON_RX_CAPTURED),
        'rx_dropped': await usb_bfm.send_etherbone_read(REG_USB_MON_RX_DROPPED),
        'tx_captured': await usb_bfm.send_etherbone_read(REG_USB_MON_TX_CAPTURED),
        'tx_dropped': await usb_bfm.send_etherbone_read(REG_USB_MON_TX_DROPPED),
        'rx_truncated': await usb_bfm.send_etherbone_read(REG_USB_MON_RX_TRUNCATED),
        'tx_truncated': await usb_bfm.send_etherbone_read(REG_USB_MON_TX_TRUNCATED),
    }


def parse_monitor_packet_safe(data: bytes):
    """Parse monitor packet, return None on failure."""
    try:
        return parse_tlp_packet(data)
    except Exception:
        return None


# =============================================================================
# Packet Drain Helper
# =============================================================================

async def drain_monitor_packets(usb_bfm: USBBFM, max_packets=1000,
                                  timeout_per_packet=100, debug_first=False) -> list:
    """
    Drain all pending monitor packets.

    Returns list of parsed TLPPacket objects.
    """
    packets = []
    first_call = True
    while len(packets) < max_packets:
        # Debug only on first call to see what's happening
        debug = debug_first and first_call
        first_call = False
        data = await usb_bfm.receive_monitor_packet(timeout_cycles=timeout_per_packet, debug=debug)
        if data is None:
            break
        pkt = parse_monitor_packet_safe(data)
        if pkt:
            packets.append(pkt)
    return packets


# =============================================================================
# STRESS TEST 1: High-Volume RX Injection
# =============================================================================

@cocotb.test()
async def test_stress_rx_flood(dut):
    """
    Flood the RX path with back-to-back TLPs.

    Tests:
    - Header FIFO capacity (256-bit entries)
    - Payload FIFO capacity
    - Capture engine under sustained load
    - Width converter throughput (256->32, 64->32)
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Inject many TLPs as fast as possible
    for i in range(STRESS_PACKET_COUNT):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + (i * 4),
            length_dw=1,
            requester_id=0x0100,
            tag=i & 0xFF,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        # Minimal gap - just 2 cycles
        await ClockCycles(dut.sys_clk, 2)

    # Allow capture pipeline to flush
    await ClockCycles(dut.sys_clk, 500)
    dut._log.info(f"Time: {get_sim_time('ns')}ns")

    # Drain and count received packets
    packets = await drain_monitor_packets(usb_bfm, max_packets=STRESS_PACKET_COUNT + 10, timeout_per_packet=500)

    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"RX Flood: injected={STRESS_PACKET_COUNT}, "
                  f"captured={stats['rx_captured']}, dropped={stats['rx_dropped']}, "
                  f"received={len(packets)}")

    total_accounted = stats['rx_captured'] + stats['rx_dropped']
    assert total_accounted == STRESS_PACKET_COUNT, \
        f"Packet accounting mismatch: captured+dropped={total_accounted}, injected={STRESS_PACKET_COUNT}"

    # Verify we can read what was captured
    assert len(packets) >= stats['rx_captured'] * 0.9, \
        f"USB receive mismatch: got {len(packets)}, expected ~{stats['rx_captured']}"


@cocotb.test()
async def test_stress_rx_flood_with_payload(dut):
    """
    Flood RX with Memory Write TLPs that have payload.

    This stresses the payload FIFO more than MRd-only traffic.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Use varying payload sizes to stress FIFO
    for i in range(STRESS_PACKET_COUNT):
        payload_size = ((i % 8) + 1) * 4  # 4 to 32 bytes
        payload = bytes([(i + j) & 0xFF for j in range(payload_size)])

        beats = TLPBuilder.memory_write_32(
            address=0x200 + (i * 64),
            data_bytes=payload,
            requester_id=0x0100,
            tag=i & 0xFF,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000010)  # BAR1
        await ClockCycles(dut.sys_clk, 4)

    await ClockCycles(dut.sys_clk, 1000)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(
        f"RX Flood (payload): captured={stats['rx_captured']}, "
        f"dropped={stats['rx_dropped']}, truncated={stats['rx_truncated']}, "
        f"received={len(packets)}"
    )
    total_accounted = stats['rx_captured'] + stats['rx_dropped']
    assert total_accounted == STRESS_PACKET_COUNT, \
        f"Packet accounting mismatch: captured+dropped={total_accounted}, injected={STRESS_PACKET_COUNT}"
    assert stats['rx_captured'] > 0, "Expected some packets to be captured"
    assert (stats['rx_dropped'] > 0) or (stats['rx_truncated'] > 0), \
        "Expected drops or truncation under payload flood"
    if stats['rx_truncated'] > 0:
        assert any(pkt.truncated for pkt in packets), \
            "Expected at least one truncated packet when rx_truncated increments"


# =============================================================================
# STRESS TEST 2: Simultaneous RX + TX Traffic
# =============================================================================

@cocotb.test()
async def test_stress_rx_tx_simultaneous(dut):
    """
    Generate both RX and TX traffic simultaneously.

    Tests arbiter's RX-priority behavior and packet atomicity.
    RX TLPs injected via PCIe, TX TLPs generated by DMA engine.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=True)

    # Start TX traffic generator (DMA reads -> generates outbound MRd TLPs)
    async def tx_generator():
        for i in range(20):
            # Configure DMA read
            await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, 0x1000 + i * 0x100)
            await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
            await usb_bfm.send_etherbone_write(REG_DMA_LEN, 64)
            await usb_bfm.send_etherbone_write(REG_DMACTL, 0x01)  # Trigger read
            await ClockCycles(dut.sys_clk, 50)

    # Start RX traffic generator
    async def rx_generator():
        for i in range(50):
            beats = TLPBuilder.memory_read_32(
                address=0x100 + i * 4,
                length_dw=1,
                requester_id=0x0100,
                tag=i & 0xFF,
            )
            await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
            await ClockCycles(dut.sys_clk, 10)

    # Run both concurrently
    tx_task = cocotb.start_soon(tx_generator())
    rx_task = cocotb.start_soon(rx_generator())

    await Combine(tx_task, rx_task)
    await ClockCycles(dut.sys_clk, 500)

    # Drain all packets
    packets = await drain_monitor_packets(usb_bfm)

    rx_packets = [p for p in packets if p.direction == Direction.RX]
    tx_packets = [p for p in packets if p.direction == Direction.TX]

    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"RX+TX Simultaneous: RX captured={len(rx_packets)}, "
                  f"TX captured={len(tx_packets)}, total={len(packets)}")
    dut._log.info(f"Stats: RX={stats['rx_captured']}/{stats['rx_dropped']}, "
                  f"TX={stats['tx_captured']}/{stats['tx_dropped']}")


# =============================================================================
# STRESS TEST 3: Backpressure Handling
# =============================================================================

@cocotb.test()
async def test_stress_backpressure_bursts(dut):
    """
    Apply intermittent backpressure while receiving packets.

    Tests that arbiter correctly handles USB side stalling mid-packet.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Backpressure control task
    async def backpressure_controller():
        for _ in range(20):
            await ClockCycles(dut.sys_clk, random.randint(50, 150))
            usb_bfm.set_backpressure(True)
            await ClockCycles(dut.sys_clk, random.randint(10, 50))
            usb_bfm.set_backpressure(False)

    # Traffic generator
    async def traffic_generator():
        for i in range(STRESS_PACKET_COUNT // 2):
            beats = TLPBuilder.memory_write_32(
                address=0x200,
                data_bytes=bytes([i & 0xFF] * 16),
                requester_id=0x0100,
                tag=i & 0xFF,
            )
            await pcie_bfm.inject_tlp(beats, bar_hit=0b000010)
            await ClockCycles(dut.sys_clk, 2)

    bp_task = cocotb.start_soon(backpressure_controller())
    tg_task = cocotb.start_soon(traffic_generator())

    await Combine(bp_task, tg_task)

    # Ensure backpressure is off for draining
    usb_bfm.set_backpressure(False)
    await ClockCycles(dut.sys_clk, 500)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Backpressure test: received={len(packets)}, "
                  f"captured={stats['rx_captured']}, dropped={stats['rx_dropped']}")

    # Should still capture most packets despite backpressure
    assert len(packets) > 0, "No packets received under backpressure"


@cocotb.test()
async def test_stress_sustained_backpressure(dut):
    """
    Apply sustained backpressure to trigger FIFO overflow.

    Verifies graceful handling of dropped packets.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Block USB output
    usb_bfm.set_backpressure(True)

    # Inject packets until FIFO overflows
    for i in range(50):
        beats = TLPBuilder.memory_read_32(
            address=0x100 + i * 4,
            length_dw=1,
            requester_id=0x0100,
            tag=i & 0xFF,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        await ClockCycles(dut.sys_clk, 5)

    await ClockCycles(dut.sys_clk, 100)

    # Release backpressure first - can't read stats via Etherbone while
    # USB is blocked because Etherbone shares the USB crossbar with monitor
    usb_bfm.set_backpressure(False)
    await ClockCycles(dut.sys_clk, 500)

    # Now check stats (Etherbone can get through)
    stats = await get_monitor_stats(usb_bfm)
    dut._log.info(f"Sustained backpressure: captured={stats['rx_captured']}, "
                  f"dropped={stats['rx_dropped']}")

    # Drain what made it through
    packets = await drain_monitor_packets(usb_bfm)
    dut._log.info(f"After release: received={len(packets)} packets")

    # Verify drop counter incremented
    assert stats['rx_dropped'] > 0 or stats['rx_captured'] < 50, \
        "Expected drops or limited capture under sustained backpressure"


# =============================================================================
# STRESS TEST 4: Etherbone + Monitor Crossbar Contention
# =============================================================================

@cocotb.test()
async def test_stress_etherbone_monitor_interleave(dut):
    """
    Interleave Etherbone CSR access with monitor packet reception.

    Tests USB crossbar arbitration between channel 0 (Etherbone) and
    channel 1 (Monitor).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    received_packets = []
    etherbone_ok = True

    # Interleave Etherbone reads with monitor packet reception
    for i in range(30):
        # Inject a TLP
        beats = TLPBuilder.memory_read_32(
            address=0x100,
            length_dw=1,
            requester_id=0x0100,
            tag=i & 0xFF,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)

        # Do an Etherbone read
        try:
            id_val = await usb_bfm.send_etherbone_read(REG_ID)
            if id_val != 0xED0113B5:
                dut._log.error(f"Etherbone read error: 0x{id_val:08X}")
                etherbone_ok = False
        except TimeoutError:
            dut._log.error("Etherbone timeout during interleave")
            etherbone_ok = False

        # Try to receive monitor packet (short timeout - may not always get one)
        pkt_data = await usb_bfm.receive_monitor_packet(timeout_cycles=100)
        if pkt_data:
            pkt = parse_monitor_packet_safe(pkt_data)
            if pkt:
                received_packets.append(pkt)

    # Drain remaining packets
    remaining = await drain_monitor_packets(usb_bfm)
    received_packets.extend(remaining)

    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Interleave test: Etherbone OK={etherbone_ok}, "
                  f"monitor packets={len(received_packets)}, "
                  f"captured={stats['rx_captured']}")

    assert etherbone_ok, "Etherbone failed during interleave"
    assert len(received_packets) > 0, "No monitor packets received"


@cocotb.test()
async def test_stress_etherbone_burst_with_monitor(dut):
    """
    Etherbone burst operations while monitor is active.

    Tests that large Etherbone transfers don't starve monitor channel.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Generate background PCIe traffic
    async def background_traffic():
        for i in range(20):
            beats = TLPBuilder.memory_read_32(
                address=0x100,
                length_dw=1,
                requester_id=0x0100,
                tag=i,
            )
            await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
            await ClockCycles(dut.sys_clk, 30)

    traffic_task = cocotb.start_soon(background_traffic())

    # Burst Etherbone reads
    for burst in range(5):
        addresses = [REG_ID, REG_DMACTL, REG_DMA_LEN, REG_USB_MON_CTRL]
        values = await usb_bfm.send_etherbone_burst_read(addresses)
        dut._log.info(f"Burst {burst}: {[f'0x{v:08X}' for v in values]}")
        await ClockCycles(dut.sys_clk, 20)

    await traffic_task
    await ClockCycles(dut.sys_clk, 200)

    packets = await drain_monitor_packets(usb_bfm)
    dut._log.info(f"Burst + monitor: received {len(packets)} monitor packets")

    assert len(packets) > 0, "Monitor starved during Etherbone bursts"


# =============================================================================
# STRESS TEST 5: Random Traffic Patterns
# =============================================================================

@cocotb.test()
async def test_stress_random_timing(dut):
    """
    Random inter-packet timing and payload sizes.

    Tests robustness against real-world traffic patterns.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    random.seed(42)  # Reproducible

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    injected_count = 0
    for i in range(STRESS_PACKET_COUNT):
        # Random: MRd or MWr
        if random.random() < 0.5:
            beats = TLPBuilder.memory_read_32(
                address=random.randint(0, 0xFFF) & ~3,
                length_dw=random.randint(1, 4),
                requester_id=0x0100,
                tag=i & 0xFF,
            )
        else:
            payload_len = random.choice([4, 8, 16, 32])
            beats = TLPBuilder.memory_write_32(
                address=random.randint(0, 0xFFF) & ~3,
                data_bytes=bytes([random.randint(0, 255) for _ in range(payload_len)]),
                requester_id=0x0100,
                tag=i & 0xFF,
            )

        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        injected_count += 1

        # Random inter-packet gap
        gap = random.randint(2, 50)
        await ClockCycles(dut.sys_clk, gap)

    await ClockCycles(dut.sys_clk, 500)

    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Random timing: injected={injected_count}, "
                  f"captured={stats['rx_captured']}, received={len(packets)}")


# =============================================================================
# STRESS TEST 6: Long-Running Stability
# =============================================================================

@cocotb.test(timeout_time=60, timeout_unit="sec")
async def test_stress_long_running(dut):
    """
    Extended run to check for memory leaks, counter wraps, etc.

    Runs for LONG_STRESS_PACKET_COUNT packets with mixed traffic.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    random.seed(12345)

    await clear_and_enable(usb_bfm, rx=True, tx=True)

    total_rx_received = 0
    total_tx_received = 0
    errors = 0

    for batch in range(LONG_STRESS_PACKET_COUNT // 10):
        # Inject 10 RX packets
        for i in range(10):
            beats = TLPBuilder.memory_read_32(
                address=0x100,
                length_dw=1,
                requester_id=0x0100,
                tag=(batch * 10 + i) & 0xFF,
            )
            await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
            await ClockCycles(dut.sys_clk, 5)

        # Quick Etherbone check
        try:
            id_val = await usb_bfm.send_etherbone_read(REG_ID)
            if id_val != 0xED0113B5:
                errors += 1
        except TimeoutError:
            errors += 1

        # Drain available packets
        packets = await drain_monitor_packets(usb_bfm, timeout_per_packet=50)
        for p in packets:
            if p.direction == Direction.RX:
                total_rx_received += 1
            else:
                total_tx_received += 1

        # Progress
        if batch % 10 == 0:
            dut._log.info(f"Batch {batch}: RX={total_rx_received}, TX={total_tx_received}")

    # Final drain
    await ClockCycles(dut.sys_clk, 500)
    packets = await drain_monitor_packets(usb_bfm)
    for p in packets:
        if p.direction == Direction.RX:
            total_rx_received += 1
        else:
            total_tx_received += 1

    stats = await get_monitor_stats(usb_bfm)

    dut._log.info(f"Long-running complete: RX received={total_rx_received}, "
                  f"TX received={total_tx_received}, errors={errors}")
    dut._log.info(f"Final stats: {stats}")

    assert errors == 0, f"Encountered {errors} errors during long run"


# =============================================================================
# STRESS TEST 7: Packet Integrity Verification
# =============================================================================

@cocotb.test()
async def test_stress_packet_integrity(dut):
    """
    Verify packet contents match what was injected.

    Uses unique patterns in each packet for identification.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Inject packets with unique identifying data
    # Note: addresses must be within BAR range (4KB = 0x000-0xFFF) because
    # the depacketizer masks addresses to BAR-relative offsets.
    expected = []
    for i in range(50):
        address = 0x100 + (i * 0x10)  # 0x100, 0x110, 0x120, ... (stays within 4KB BAR)
        tag = i

        beats = TLPBuilder.memory_read_32(
            address=address,
            length_dw=1,
            requester_id=0x0100,
            tag=tag,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000001)
        expected.append({'address': address, 'tag': tag})
        await ClockCycles(dut.sys_clk, 20)

    await ClockCycles(dut.sys_clk, 500)

    # Receive and verify
    packets = await drain_monitor_packets(usb_bfm)
    stats = await get_monitor_stats(usb_bfm)

    # Debug: show first few packets
    for i, pkt in enumerate(packets[:5]):
        dut._log.info(f"Received pkt {i}: addr=0x{pkt.address:08x}, tag={pkt.tag}")
    if expected[:5]:
        dut._log.info(f"Expected first 5: {expected[:5]}")

    matched = 0
    for pkt in packets:
        for exp in expected:
            if pkt.address == exp['address'] and pkt.tag == exp['tag']:
                matched += 1
                break

    dut._log.info(f"Integrity check: {matched}/{len(expected)} packets matched")
    total_accounted = stats['rx_captured'] + stats['rx_dropped']
    assert total_accounted == len(expected), \
        f"Packet accounting mismatch: captured+dropped={total_accounted}, injected={len(expected)}"

    # Allow some drops but most should match
    assert matched >= len(expected) * 0.9, \
        f"Too many packet mismatches: {matched}/{len(expected)}"


# =============================================================================
# STRESS TEST 8: Width Converter Edge Cases
# =============================================================================

@cocotb.test()
async def test_stress_width_converter_boundary(dut):
    """
    Test with payload sizes that stress the 64->32 width converter.

    Particularly interested in odd DWORD counts and boundary conditions.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    await clear_and_enable(usb_bfm, rx=True, tx=False)

    # Test various payload sizes
    test_sizes = [4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 60, 64]

    for size in test_sizes:
        payload = bytes([i & 0xFF for i in range(size)])

        beats = TLPBuilder.memory_write_32(
            address=0x200,
            data_bytes=payload,
            requester_id=0x0100,
            tag=size & 0xFF,
        )
        await pcie_bfm.inject_tlp(beats, bar_hit=0b000010)
        await ClockCycles(dut.sys_clk, 50)

        # Receive and verify
        pkt_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)
        assert pkt_data is not None, f"Expected to capture packet for size {size}"

        pkt = parse_monitor_packet_safe(pkt_data)
        assert pkt is not None, f"Failed to parse packet for size {size}"

        expected_dw = (size + 3) // 4
        dut._log.info(
            f"Size {size}: captured payload_length={pkt.payload_length}, "
            f"expected={expected_dw}"
        )
        assert pkt.payload_length == expected_dw, \
            f"Payload length mismatch for size {size}: got {pkt.payload_length}, expected {expected_dw}"

    stats = await get_monitor_stats(usb_bfm)
    dut._log.info(f"Width converter test: {stats}")
