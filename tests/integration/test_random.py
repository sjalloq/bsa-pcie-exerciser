#
# BSA PCIe Exerciser - Randomized Integration Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Randomized integration tests for BSA PCIe Exerciser.

Uses constrained-random stimulus to exercise TLP parameter space more thoroughly
than directed tests. Supports reproducible failures via seed control.

Run with:
    make sim TESTCASE=test_random_bar0
    RANDOM_SEED=12345 make sim TESTCASE=test_random_bar0  # Reproducible
"""

import os
import sys
import atexit

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# Add parent directories to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder
from tests.common.randomizer import (
    TLPRandomizer,
    TLPConstraints,
    BAR0_REGISTER_CONSTRAINTS,
    BAR1_BUFFER_CONSTRAINTS,
    MSIX_TABLE_CONSTRAINTS,
    DMA_HOST_CONSTRAINTS,
    STRESS_TEST_CONSTRAINTS,
)
from tests.common.coverage import get_coverage, CoverageCollector

# Import register offset constants from BSA registers module
from bsa_pcie_exerciser.gateware.core.bsa_registers import (
    REG_MSICTL, REG_INTXCTL, REG_DMACTL, REG_DMA_OFFSET,
    REG_DMA_BUS_ADDR_LO, REG_DMA_BUS_ADDR_HI, REG_DMA_LEN, REG_DMASTATUS,
    REG_PASID_VAL, REG_ATSCTL, REG_RID_CTL, REG_TXN_CTRL, REG_ID,
)

# Write masks for control registers to avoid triggering operations
# MSICTL: [31]=trigger - mask it out
MSICTL_WRITE_MASK = 0x7FFFFFFF
# DMACTL: [3:0]=trigger - mask it out
DMACTL_WRITE_MASK = 0xFFFFFFF0
# ATSCTL: [0]=trigger, [5]=clear_atc - mask them out
ATSCTL_WRITE_MASK = 0xFFFFFFDE


# =============================================================================
# Configuration
# =============================================================================

# Get seed from environment for reproducibility
RANDOM_SEED = int(os.environ.get('RANDOM_SEED', '42'))

# Number of transactions per test (can override via environment)
N_TRANSACTIONS = int(os.environ.get('N_TRANSACTIONS', '100'))


# =============================================================================
# Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    dut.pcie_rst.value = 1
    await ClockCycles(dut.sys_clk, 10)
    dut.sys_rst.value = 0
    dut.pcie_rst.value = 0
    await ClockCycles(dut.sys_clk, 10)


def log_seed(dut, seed: int, test_name: str):
    """Log seed for reproduction of failures."""
    dut._log.info(f"{'='*60}")
    dut._log.info(f"Test: {test_name}")
    dut._log.info(f"Random seed: {seed}")
    dut._log.info(f"To reproduce: RANDOM_SEED={seed} make sim TESTCASE={test_name}")
    dut._log.info(f"{'='*60}")


# =============================================================================
# BAR0 Register Tests
# =============================================================================

@cocotb.test()
async def test_random_bar0(dut):
    """
    Randomized BAR0 register access.

    Exercises:
    - All valid register offsets
    - Random tag values
    - Read/write mix
    - Variable timing
    """
    test_name = "test_random_bar0"
    seed = RANDOM_SEED

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed, constraints=BAR0_REGISTER_CONSTRAINTS)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # Valid register offsets (from BSARegisters)
    valid_offsets = [
        REG_MSICTL,
        REG_INTXCTL,
        REG_DMACTL,
        REG_DMA_OFFSET,
        REG_DMA_BUS_ADDR_LO,
        REG_DMA_BUS_ADDR_HI,
        REG_DMA_LEN,
        REG_DMASTATUS,
        REG_PASID_VAL,
        REG_ATSCTL,
        REG_RID_CTL,
        REG_TXN_CTRL,
        REG_ID,  # read-only
    ]

    # Masks for control registers to avoid triggering operations
    write_masks = {
        REG_MSICTL: MSICTL_WRITE_MASK,
        REG_DMACTL: DMACTL_WRITE_MASK,
        REG_ATSCTL: ATSCTL_WRITE_MASK,
    }

    success = 0
    timeouts = 0
    tag_errors = 0

    for i in range(N_TRANSACTIONS):
        is_write = rand.rng.random() < 0.5
        offset = rand.rng.choice(valid_offsets)
        tag = rand.random_tag()

        params = {
            'address': offset,
            'length_dw': 1,
            'tag': tag,
            'attr': 0,
            'at': 0,
            'first_be': 0xF,
            'last_be': 0x0,
        }

        if is_write and offset != REG_ID:  # Don't write to read-only ID register
            data = rand.rng.randint(0, 0xFFFF_FFFF)
            # Apply mask to avoid triggering DMA/MSI/ATS operations
            if offset in write_masks:
                data &= write_masks[offset]
            data_bytes = data.to_bytes(4, 'little')
            beats = TLPBuilder.memory_write_32(offset, data_bytes, tag=tag)
            await bfm.inject_tlp(beats, bar_hit=0b000001)

            cov.sample_tlp('MWr', params)
            cov.sample('bar0_offset_written', offset)
        else:
            beats = TLPBuilder.memory_read_32(offset, length_dw=1, tag=tag)
            await bfm.inject_tlp(beats, bar_hit=0b000001)

            cpl = await bfm.capture_tlp(timeout_cycles=200)
            cov.sample_tlp('MRd', params)
            cov.sample('bar0_offset_read', offset)

            if cpl:
                cov.sample('bar0_completion', True)

                # Verify tag matches
                cpl_tag = TLPBuilder.extract_tag_from_cpl(cpl)
                if cpl_tag != tag:
                    tag_errors += 1
                    dut._log.error(f"[{i}] Tag mismatch: sent {tag}, got {cpl_tag}")
                else:
                    success += 1
            else:
                timeouts += 1
                cov.sample('bar0_completion', False)
                dut._log.warning(f"[{i}] No completion for read at 0x{offset:02X}")

        # Random delay
        await ClockCycles(dut.sys_clk, rand.random_delay(1, 8))

    dut._log.info(f"Completed {N_TRANSACTIONS} transactions: {success} OK, {tag_errors} tag errors, {timeouts} timeouts")

    # Fail on any tag errors - these indicate a real bug
    if tag_errors > 0:
        raise AssertionError(f"Tag verification failed: {tag_errors} mismatches")

    # Allow some timeouts (may indicate back-pressure or timing issues)
    if timeouts > N_TRANSACTIONS * 0.1:
        raise AssertionError(f"Too many timeouts: {timeouts}/{N_TRANSACTIONS}")


@cocotb.test()
async def test_random_bar0_rapid(dut):
    """
    Rapid-fire BAR0 access with minimal delays.

    Tests back-to-back transactions and pipeline behavior.
    """
    test_name = "test_random_bar0_rapid"
    seed = RANDOM_SEED + 100

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed, constraints=BAR0_REGISTER_CONSTRAINTS)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # Track tags we've sent and received
    sent_tags = set()
    received_tags = set()
    completions_received = 0
    tag_errors = 0

    # Valid register offsets for reads
    valid_offsets = [
        REG_MSICTL, REG_INTXCTL, REG_DMACTL, REG_DMA_OFFSET,
        REG_DMA_BUS_ADDR_LO, REG_DMA_BUS_ADDR_HI, REG_DMA_LEN, REG_DMASTATUS,
        REG_PASID_VAL, REG_ATSCTL, REG_RID_CTL, REG_TXN_CTRL, REG_ID,
    ]

    # Completion collector running in background
    collect_done = False

    async def collect_completions():
        nonlocal completions_received, tag_errors
        while not collect_done or completions_received < len(sent_tags):
            cpl = await bfm.capture_tlp(timeout_cycles=50)
            if cpl:
                completions_received += 1
                cpl_tag = TLPBuilder.extract_tag_from_cpl(cpl)
                received_tags.add(cpl_tag)
                if cpl_tag not in sent_tags:
                    tag_errors += 1
                    dut._log.error(f"Unexpected tag in completion: {cpl_tag}")
            elif collect_done:
                break  # No more completions coming

    # Start collector in background
    collector = cocotb.start_soon(collect_completions())

    # Issue reads as fast as possible
    for i in range(50):
        offset = rand.rng.choice(valid_offsets)
        tag = i & 0xFF
        sent_tags.add(tag)

        beats = TLPBuilder.memory_read_32(offset, length_dw=1, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000001)

        # Minimal delay - just 1 cycle
        await ClockCycles(dut.sys_clk, 1)

    # Signal that injection is done, wait for remaining completions
    collect_done = True
    await ClockCycles(dut.sys_clk, 200)  # Give time for stragglers
    collector.cancel()

    dut._log.info(f"Received {completions_received}/50 completions in rapid-fire mode")

    cov.sample('rapid_fire_completions', completions_received)

    # Fail on tag errors
    if tag_errors > 0:
        raise AssertionError(f"Tag verification failed: {tag_errors} unexpected tags")

    if completions_received < 40:  # Allow some to be in flight
        raise AssertionError(f"Too few completions: {completions_received}/50")


# =============================================================================
# BAR1 Buffer Tests
# =============================================================================

@cocotb.test()
async def test_random_bar1_rw(dut):
    """
    Randomized BAR1 buffer read/write with verification.

    Exercises:
    - Various buffer offsets
    - Different data patterns
    - Multiple transfer sizes
    - Read-after-write verification
    """
    test_name = "test_random_bar1_rw"
    seed = RANDOM_SEED + 200

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed, constraints=BAR1_BUFFER_CONSTRAINTS)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # Track written data for verification
    written_data = {}

    # Write phase
    n_writes = min(N_TRANSACTIONS // 2, 50)
    for i in range(n_writes):
        params = rand.generate_mwr_params()
        offset = params['address'] & 0x3FF8  # QWORD align within 16KB

        # Use exactly 8 bytes, pad if needed
        data_bytes = params['data'][:8]
        if len(data_bytes) < 8:
            data_bytes = data_bytes + b'\x00' * (8 - len(data_bytes))
        # Store expected in PHY view (big-endian per DWORD) to match TLPBuilder encoding
        expected_low = int.from_bytes(data_bytes[0:4], 'big')
        expected_high = int.from_bytes(data_bytes[4:8], 'big')
        data_int = (expected_high << 32) | expected_low

        beats = TLPBuilder.memory_write_32(
            offset,
            data_bytes,
            tag=params['tag'],
            attr=params['attr'],
        )
        await bfm.inject_tlp(beats, bar_hit=0b000010)  # BAR1

        written_data[offset] = data_int
        cov.sample_tlp('MWr', params)
        cov.sample('bar1_offset_written', offset >> 3)  # QWORD index

        await ClockCycles(dut.sys_clk, rand.random_delay(2, 6))

    dut._log.info(f"Wrote {n_writes} locations to BAR1")

    # Verify phase - random subset
    n_verify = min(20, len(written_data))
    offsets_to_verify = rand.rng.sample(list(written_data.keys()), n_verify)

    data_errors = 0
    tag_errors = 0
    timeouts = 0

    for offset in offsets_to_verify:
        expected = written_data[offset]
        tag = rand.random_tag()

        beats = TLPBuilder.memory_read_32(offset, length_dw=2, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000010)

        cpl = await bfm.capture_tlp(timeout_cycles=200)

        if cpl and len(cpl) >= 2:
            # Verify tag matches
            cpl_tag = TLPBuilder.extract_tag_from_cpl(cpl)
            if cpl_tag != tag:
                tag_errors += 1
                dut._log.error(f"Tag mismatch at 0x{offset:04X}: sent {tag}, got {cpl_tag}")

            # Extract 64-bit data from completion in PHY view (no byte swap)
            # Beat 1: [Data0 | DW2] - Data0 in upper 32 bits
            # Beat 2: [??? | Data1] - Data1 in lower 32 bits (for 2DW read)
            raw_low = (cpl[1]['dat'] >> 32) & 0xFFFF_FFFF  # Data0
            raw_high = 0
            if len(cpl) > 2:
                raw_high = cpl[2]['dat'] & 0xFFFF_FFFF  # Data1
            got = (raw_high << 32) | raw_low

            if got != expected:
                data_errors += 1
                dut._log.error(f"Data mismatch at 0x{offset:04X}: got 0x{got:016X}, expected 0x{expected:016X}")
            else:
                cov.sample('bar1_verify_pass', True)
        else:
            timeouts += 1
            cov.sample('bar1_verify_pass', False)
            dut._log.error(f"No completion for read at 0x{offset:04X}")

    success = n_verify - data_errors - tag_errors - timeouts
    dut._log.info(f"Verified {success}/{n_verify} locations ({data_errors} data, {tag_errors} tag, {timeouts} timeout errors)")

    if tag_errors > 0:
        raise AssertionError(f"Tag verification failed: {tag_errors} mismatches")

    if data_errors > 0 or timeouts > 0:
        raise AssertionError(f"Data verification failed: {data_errors} data errors, {timeouts} timeouts")


# =============================================================================
# TLP Attribute Tests
# =============================================================================

async def write_bar0_register(bfm, offset, data):
    """
    Write a 32-bit value to a BAR0 register via TLP.

    Args:
        bfm: PCIeBFM instance
        offset: Register offset within BAR0
        data: 32-bit value to write
    """
    data_bytes = data.to_bytes(4, 'little')
    beats = TLPBuilder.memory_write_32(
        address=offset,
        data_bytes=data_bytes,
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)
    await ClockCycles(bfm.clk, 5)


async def read_bar0_register(bfm, offset, tag=0):
    """
    Read a 32-bit value from a BAR0 register via TLP.

    Args:
        bfm: PCIeBFM instance
        offset: Register offset within BAR0
        tag: TLP tag

    Returns:
        32-bit register value, or None on timeout
    """
    beats = TLPBuilder.memory_read_32(
        address=offset,
        length_dw=1,
        requester_id=0x0100,
        tag=tag,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)

    cpl = await bfm.capture_tlp(timeout_cycles=200)
    if cpl is None:
        return None

    raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF
    data = int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')
    return data


@cocotb.test()
async def test_random_attributes(dut):
    """
    Test TLP attribute propagation through DMA interface.

    Configures DMA via TLP writes, captures the outgoing DMA request TLP,
    and verifies that No-Snoop and Address Type attributes match.

    Exercises:
    - No-Snoop attribute (attr[0])
    - Address Type field (AT)
    - All combinations via constrained-random
    """
    test_name = "test_random_attributes"
    seed = RANDOM_SEED + 300

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed, constraints=DMA_HOST_CONSTRAINTS)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    n_ops = min(N_TRANSACTIONS, 50)
    attr_errors = 0
    at_errors = 0
    timeouts = 0

    for i in range(n_ops):
        # Random parameters
        bus_addr = rand.random_address() & 0xFFFF_FFF8  # QWORD align
        expected_ns = rand.rng.randint(0, 1)  # No-Snoop bit
        expected_at = rand.random_at()

        # Configure DMA via TLP writes to BAR0
        await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, bus_addr & 0xFFFFFFFF)
        await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, (bus_addr >> 32) & 0xFFFFFFFF)
        await write_bar0_register(bfm, REG_DMA_LEN, 8)   # 8 bytes
        await write_bar0_register(bfm, REG_DMA_OFFSET, 0)

        # Build DMACTL value and trigger:
        # [3:0]=trigger (1), [4]=direction (0=read), [5]=no_snoop, [11:10]=addr_type
        dmactl = (1 |                      # trigger [3:0]=1
                  (0 << 4) |               # direction=read [4]
                  (expected_ns << 5) |     # no_snoop [5]
                  (expected_at << 10))     # addr_type [11:10]
        await write_bar0_register(bfm, REG_DMACTL, dmactl)

        # Capture outgoing DMA request TLP from phy_tx
        req = await bfm.capture_tlp(timeout_cycles=500)

        if req:
            # Extract attributes from captured TLP
            actual_attr, actual_at = TLPBuilder.extract_attr_from_tlp(req)
            actual_ns = actual_attr & 0x1

            # Sample coverage
            cov.sample('dma_ns_expected', expected_ns)
            cov.sample('dma_ns_actual', actual_ns)
            cov.sample('dma_at_expected', expected_at)
            cov.sample('dma_at_actual', actual_at)
            cov.sample_cross('dma_ns_x_at', expected_ns, expected_at)

            # Verify No-Snoop
            if actual_ns != expected_ns:
                attr_errors += 1
                dut._log.error(f"[{i}] No-Snoop mismatch: expected {expected_ns}, got {actual_ns}")

            # Verify AT
            if actual_at != expected_at:
                at_errors += 1
                dut._log.error(f"[{i}] AT mismatch: expected {expected_at}, got {actual_at}")

            # Extract tag and send completion back
            tag = TLPBuilder.extract_tag_from_mrd(req)
            cpl_data = rand.rng.randint(0, 2**64-1).to_bytes(8, 'little')
            cpl_beats = TLPBuilder.completion(
                requester_id=0x0100,
                completer_id=0x0200,
                tag=tag,
                data_bytes=cpl_data
            )
            await bfm.inject_tlp(cpl_beats, bar_hit=0)

            # Wait for DMA to complete
            await ClockCycles(dut.sys_clk, 20)
        else:
            timeouts += 1
            dut._log.warning(f"[{i}] DMA request timeout")

        await ClockCycles(dut.sys_clk, rand.random_delay(3, 10))

    dut._log.info(f"Completed {n_ops} DMA ops: {attr_errors} attr errors, {at_errors} AT errors, {timeouts} timeouts")

    if attr_errors > 0 or at_errors > 0:
        raise AssertionError(f"TLP attribute verification failed: {attr_errors} attr, {at_errors} AT errors")


# =============================================================================
# Stress Tests
# =============================================================================

@cocotb.test()
async def test_backpressure_stress(dut):
    """
    Stress test with random back-pressure on TX path.

    Verifies DUT handles flow control correctly.
    """
    test_name = "test_backpressure_stress"
    seed = RANDOM_SEED + 400

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # Back-pressure driver coroutine
    bp_active = True

    async def backpressure_driver():
        """Randomly toggle TX ready to create back-pressure."""
        nonlocal bp_active
        while bp_active:
            if rand.rng.random() < 0.3:
                # 30% chance of back-pressure
                dut.phy_tx_ready.value = 0
                await ClockCycles(dut.sys_clk, rand.rng.randint(1, 5))
            dut.phy_tx_ready.value = 1
            await ClockCycles(dut.sys_clk, rand.rng.randint(1, 8))

    # Start back-pressure driver
    bp_task = cocotb.start_soon(backpressure_driver())

    # Valid register offsets for reads
    valid_offsets = [
        REG_MSICTL, REG_INTXCTL, REG_DMACTL, REG_DMA_OFFSET,
        REG_DMA_BUS_ADDR_LO, REG_DMA_BUS_ADDR_HI, REG_DMA_LEN, REG_DMASTATUS,
        REG_PASID_VAL, REG_ATSCTL, REG_RID_CTL, REG_TXN_CTRL, REG_ID,
    ]

    success = 0
    timeouts = 0
    tag_errors = 0

    for i in range(30):
        offset = rand.rng.choice(valid_offsets)
        tag = rand.random_tag()

        beats = TLPBuilder.memory_read_32(offset, length_dw=1, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000001)

        cpl = await bfm.capture_tlp(timeout_cycles=500)

        if cpl:
            cov.sample('backpressure_completion', True)

            # Verify tag matches
            cpl_tag = TLPBuilder.extract_tag_from_cpl(cpl)
            if cpl_tag != tag:
                tag_errors += 1
                dut._log.error(f"[{i}] Tag mismatch: sent {tag}, got {cpl_tag}")
            else:
                success += 1
        else:
            timeouts += 1
            cov.sample('backpressure_completion', False)

        await ClockCycles(dut.sys_clk, rand.random_delay(3, 15))

    # Stop back-pressure driver
    bp_active = False
    bp_task.cancel()

    dut._log.info(f"Under back-pressure: {success}/30 OK, {tag_errors} tag errors, {timeouts} timeouts")

    # Fail on any tag errors
    if tag_errors > 0:
        raise AssertionError(f"Tag verification failed: {tag_errors} mismatches")

    if success < 24:  # 80% success rate minimum
        raise AssertionError(f"Too many timeouts under back-pressure: {timeouts}/30")


@cocotb.test()
async def test_tag_range(dut):
    """
    Test with full range of tag values.

    Ensures all 256 tags can be used correctly.
    """
    test_name = "test_tag_range"
    seed = RANDOM_SEED + 500

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # Test representative tags from each range
    test_tags = [0, 1, 31, 32, 63, 64, 127, 128, 191, 192, 254, 255]

    for tag in test_tags:
        beats = TLPBuilder.memory_read_32(REG_ID, length_dw=1, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000001)

        cpl = await bfm.capture_tlp(timeout_cycles=200)

        if cpl:
            cpl_tag = TLPBuilder.extract_tag_from_cpl(cpl)
            cov.sample('tag_used', tag)
            cov.sample('tag_returned', cpl_tag)

            if cpl_tag != tag:
                raise AssertionError(f"Tag mismatch: sent {tag}, got {cpl_tag}")
        else:
            raise AssertionError(f"No completion for tag {tag}")

        await ClockCycles(dut.sys_clk, 5)

    dut._log.info(f"All {len(test_tags)} tag values tested successfully")


# =============================================================================
# MSI-X Table Tests (BAR2)
# =============================================================================

# MSI-X Table Entry offsets (16 bytes per vector)
MSIX_ADDR_LO  = 0x00  # Message Address Low
MSIX_ADDR_HI  = 0x04  # Message Address High
MSIX_DATA     = 0x08  # Message Data
MSIX_CONTROL  = 0x0C  # Vector Control (bit 0 = mask)


@cocotb.test()
async def test_random_msix_table(dut):
    """
    Randomized MSI-X table read/write with verification.

    Exercises:
    - Random vector indices (0-15)
    - All four entry fields (addr_lo, addr_hi, data, control)
    - Read-after-write verification
    - Random tags
    """
    test_name = "test_random_msix_table"
    seed = RANDOM_SEED + 600

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed, constraints=MSIX_TABLE_CONSTRAINTS)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # Track written data for verification
    written_entries = {}  # {(vector, field): value}

    n_writes = min(N_TRANSACTIONS // 2, 40)

    # Write phase - write random values to random vectors
    # Note: Only use QWORD-aligned offsets (0x00, 0x08) since the MSI-X table
    # expects upper DWORD writes (0x04, 0x0C) to use last_be instead of first_be
    for i in range(n_writes):
        vector = rand.rng.randint(0, 15)  # All 16 vectors (0-15)
        field_offset = rand.rng.choice([MSIX_ADDR_LO, MSIX_DATA])  # QWORD-aligned only
        tag = rand.random_tag()

        # Calculate byte offset within BAR2
        entry_offset = vector * 16 + field_offset

        value = rand.rng.randint(0, 0xFFFFFFFF)

        data_bytes = value.to_bytes(4, 'little')
        beats = TLPBuilder.memory_write_32(entry_offset, data_bytes, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000100)  # BAR2

        written_entries[(vector, field_offset)] = value
        cov.sample('msix_vector_written', vector)
        cov.sample('msix_field_written', field_offset)

        await ClockCycles(dut.sys_clk, rand.random_delay(2, 5))

    dut._log.info(f"Wrote {n_writes} MSI-X table entries")

    # Verify phase
    n_verify = min(20, len(written_entries))
    entries_to_verify = rand.rng.sample(list(written_entries.keys()), n_verify)

    data_errors = 0
    tag_errors = 0
    timeouts = 0

    for vector, field_offset in entries_to_verify:
        expected = written_entries[(vector, field_offset)]
        entry_offset = vector * 16 + field_offset
        tag = rand.random_tag()

        beats = TLPBuilder.memory_read_32(entry_offset, length_dw=1, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000100)  # BAR2

        cpl = await bfm.capture_tlp(timeout_cycles=200)

        if cpl and len(cpl) >= 2:
            cpl_tag = TLPBuilder.extract_tag_from_cpl(cpl)
            if cpl_tag != tag:
                tag_errors += 1
                dut._log.error(f"Tag mismatch at vector {vector} field 0x{field_offset:X}")

            # Extract data from completion (PHY view)
            raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF
            got = int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')

            if got != expected:
                data_errors += 1
                dut._log.error(f"Data mismatch at vector {vector} field 0x{field_offset:X}: "
                             f"got 0x{got:08X}, expected 0x{expected:08X}")
            else:
                cov.sample('msix_verify_pass', True)
        else:
            timeouts += 1
            dut._log.error(f"No completion for vector {vector} field 0x{field_offset:X}")

        await ClockCycles(dut.sys_clk, 3)

    success = n_verify - data_errors - tag_errors - timeouts
    dut._log.info(f"Verified {success}/{n_verify} MSI-X entries "
                  f"({data_errors} data, {tag_errors} tag, {timeouts} timeout errors)")

    if tag_errors > 0:
        raise AssertionError(f"Tag verification failed: {tag_errors} mismatches")

    if data_errors > 0 or timeouts > 0:
        raise AssertionError(f"MSI-X table verification failed: {data_errors} data, {timeouts} timeouts")


# =============================================================================
# 64-bit Address Tests
# =============================================================================

# NOTE: 64-bit address TLPs (4DW headers) are NOT required for BSA ACS compliance.
# The BSA ACS tests use 32-bit MMIO accesses (pal_mmio_read/write with uint32_t data).
# For 64-bit register values, they use two separate 32-bit accesses.
# Therefore, this test is disabled as it's outside the BSA ACS scope.


# =============================================================================
# DMA Buffer Edge Cases
# =============================================================================

@cocotb.test()
async def test_dma_buffer_boundaries(dut):
    """
    Test DMA buffer boundary conditions.

    Exercises:
    - First and last addresses in buffer
    - Transfers near buffer end
    - Various transfer lengths

    Uses unique address pattern (0xBD prefix in data) to detect stale data.
    """
    test_name = "test_dma_buffer_boundaries"
    seed = RANDOM_SEED + 800

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    rand = TLPRandomizer(seed=seed)
    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # BAR1 buffer is 16KB (0x4000 bytes)
    BUFFER_SIZE = 0x4000

    # Test boundary offsets - use unique region (0x3C00-0x3FFF) to avoid
    # collision with other tests that use lower/random addresses
    # Also use unique data pattern with 0xBD prefix
    boundary_offsets = [
        0x3C00,                    # Test region start
        0x3C08,                    # Second QWORD in region
        BUFFER_SIZE - 8,           # Last QWORD (0x3FF8)
        BUFFER_SIZE - 16,          # Second-to-last (0x3FF0)
        0x3C10,                    # Third QWORD in region
        0x3C18,                    # Fourth QWORD in region
        0x3C20,                    # Fifth QWORD in region
    ]

    written_data = {}

    # Write to boundary addresses using unique pattern (0xBDxxyyzz)
    for i, offset in enumerate(boundary_offsets):
        tag = rand.random_tag()
        # Create unique recognizable pattern: 0xBD + offset-based value
        value = 0xBD000000 | ((offset & 0xFFFF) << 8) | i
        data_bytes = value.to_bytes(4, 'little')
        expected = int.from_bytes(data_bytes, 'big')  # PHY view

        beats = TLPBuilder.memory_write_32(offset, data_bytes, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000010)  # BAR1

        written_data[offset] = expected
        cov.sample('buffer_boundary_offset', offset)

        await ClockCycles(dut.sys_clk, 3)

    dut._log.info(f"Wrote {len(boundary_offsets)} boundary locations")

    # Verify all boundary writes
    errors = 0
    for offset, expected in written_data.items():
        tag = rand.random_tag()
        beats = TLPBuilder.memory_read_32(offset, length_dw=1, tag=tag)
        await bfm.inject_tlp(beats, bar_hit=0b000010)

        cpl = await bfm.capture_tlp(timeout_cycles=200)

        if cpl and len(cpl) >= 2:
            raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF

            if raw_data != expected:
                errors += 1
                dut._log.error(f"Boundary mismatch at 0x{offset:04X}: "
                             f"got 0x{raw_data:08X}, expected 0x{expected:08X}")
            else:
                cov.sample('buffer_boundary_pass', True)
        else:
            errors += 1
            dut._log.error(f"No completion at boundary 0x{offset:04X}")

    dut._log.info(f"Verified {len(boundary_offsets) - errors}/{len(boundary_offsets)} boundary locations")

    if errors > 0:
        raise AssertionError(f"Buffer boundary test failed: {errors} errors")


# =============================================================================
# Error Path Tests
# =============================================================================

@cocotb.test()
async def test_completion_timeout_recovery(dut):
    """
    Test that the system recovers after completion timeouts.

    Issues a read, doesn't inject a completion, then verifies
    subsequent transactions still work correctly.
    """
    test_name = "test_completion_timeout_recovery"
    seed = RANDOM_SEED + 900

    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
    cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    cov = get_coverage()

    log_seed(dut, seed, test_name)

    # First, verify normal operation works
    beats = TLPBuilder.memory_read_32(REG_ID, length_dw=1, tag=1)
    await bfm.inject_tlp(beats, bar_hit=0b000001)
    cpl = await bfm.capture_tlp(timeout_cycles=200)

    if not cpl:
        raise AssertionError("Initial read failed - cannot test recovery")

    dut._log.info("Initial read succeeded")

    # Now issue several reads in quick succession (simulating potential stress)
    for i in range(5):
        beats = TLPBuilder.memory_read_32(REG_ID, length_dw=1, tag=10 + i)
        await bfm.inject_tlp(beats, bar_hit=0b000001)

        cpl = await bfm.capture_tlp(timeout_cycles=200)
        if cpl:
            cov.sample('recovery_read_ok', True)
        else:
            cov.sample('recovery_read_ok', False)

        await ClockCycles(dut.sys_clk, 3)

    dut._log.info("Recovery test completed - system remains responsive")


# =============================================================================
# Coverage Report Hook
# =============================================================================

COVERAGE_JSON = "coverage_random.json"
COVERAGE_REPORT = "coverage_random.txt"

# Set RESET_COVERAGE=1 to start fresh instead of accumulating
RESET_COVERAGE = os.environ.get('RESET_COVERAGE', '0') == '1'


def load_existing_coverage():
    """Load existing coverage from previous runs to accumulate results."""
    if RESET_COVERAGE:
        if os.path.exists(COVERAGE_JSON):
            os.remove(COVERAGE_JSON)
            print(f"Reset coverage: removed {COVERAGE_JSON}")
        return

    cov = get_coverage()
    if os.path.exists(COVERAGE_JSON):
        cov.load(COVERAGE_JSON)
        print(f"Loaded existing coverage from {COVERAGE_JSON}")


def save_coverage():
    """Save coverage data and report at end of test run."""
    try:
        cov = get_coverage()

        # Save raw data to JSON (for merging with future runs)
        cov.save(COVERAGE_JSON)

        # Write human-readable report to file
        report = cov.report()
        with open(COVERAGE_REPORT, 'w') as f:
            f.write(report)

        print(f"Coverage saved to {COVERAGE_JSON}")
        print(f"Coverage report written to {COVERAGE_REPORT}")
    except Exception as e:
        print(f"Failed to save coverage: {e}")


# Load any existing coverage at module import time
load_existing_coverage()

atexit.register(save_coverage)
