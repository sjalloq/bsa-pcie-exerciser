#
# Etherbone Basic Tests
#
# Copyright (c) 2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Tests for the USBEtherbone module using LiteEth components.
#

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tbench.common.usb_bfm import USBBFM


# =============================================================================
# Test Constants
# =============================================================================

# ID register value in WishboneSRAM
ID_VALUE = 0xED0113B5
ID_ADDRESS = 0x1000  # Address of ID register


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset and initialize clocks."""
    # Start system clock (100MHz = 10ns period)
    cocotb.start_soon(Clock(dut.sys_clk, 10, unit="ns").start())

    # Apply reset
    dut.sys_rst.value = 1

    # Initialize USB signals
    dut.usb_inject_valid.value = 0
    dut.usb_capture_ready.value = 1
    dut.usb_tx_backpressure.value = 0

    await ClockCycles(dut.sys_clk, 20)

    # Release reset
    dut.sys_rst.value = 0

    await ClockCycles(dut.sys_clk, 50)


# =============================================================================
# Probe Tests
# =============================================================================

@cocotb.test()
async def test_probe_response(dut):
    """
    Send Etherbone probe request and verify response.

    The probe mechanism is used for device discovery.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Debug: show what we're sending
    packet = usb_bfm._build_etherbone_packet(pf=True)
    dut._log.info(f"Probe packet bytes: {packet.hex()}")
    dut._log.info(f"Probe packet length: {len(packet)} bytes")

    # Send probe request
    await usb_bfm.send_etherbone_probe()
    dut._log.info("Probe request sent, waiting for response...")

    # Wait for probe response with debug
    got_response = await usb_bfm.wait_etherbone_probe_response(timeout_cycles=2000, debug=True)

    assert got_response, "Expected probe response, got timeout"
    dut._log.info("Probe response received successfully")


# =============================================================================
# Single Read/Write Tests
# =============================================================================

@cocotb.test()
async def test_single_read(dut):
    """
    Read ID register via Etherbone.

    The ID register is at address 0x1000 and returns 0xED0113B5.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # Read ID register
    value = await usb_bfm.send_etherbone_read(ID_ADDRESS, timeout_cycles=5000)

    assert value == ID_VALUE, f"Expected 0x{ID_VALUE:08X}, got 0x{value:08X}"
    dut._log.info(f"ID register read successful: 0x{value:08X}")


@cocotb.test()
async def test_single_write(dut):
    """
    Write and read back a value from SRAM via Etherbone.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    test_addr = 0x100
    test_value = 0xCAFEBABE

    # Write value
    await usb_bfm.send_etherbone_write(test_addr, test_value)

    # Read back
    readback = await usb_bfm.send_etherbone_read(test_addr, timeout_cycles=5000)

    assert readback == test_value, f"Expected 0x{test_value:08X}, got 0x{readback:08X}"
    dut._log.info(f"Write/read 0x{test_value:08X} to address 0x{test_addr:03X} successful")


@cocotb.test()
async def test_multiple_writes(dut):
    """
    Multiple sequential writes and reads to verify protocol state machine.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    test_values = [0x12345678, 0xDEADBEEF, 0x00000000, 0xFFFFFFFF]
    test_addr = 0x200

    for i, test_value in enumerate(test_values):
        # Write
        await usb_bfm.send_etherbone_write(test_addr, test_value)

        # Read back
        readback = await usb_bfm.send_etherbone_read(test_addr, timeout_cycles=5000)

        assert readback == test_value, \
            f"Iteration {i}: wrote 0x{test_value:08X}, got 0x{readback:08X}"

    dut._log.info(f"Multiple write/read test passed ({len(test_values)} iterations)")


# =============================================================================
# Burst Tests
# =============================================================================

@cocotb.test()
async def test_burst_read(dut):
    """
    Burst read multiple addresses.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    # First, write some test data
    base_addr = 0x300
    test_values = [0x11111111, 0x22222222, 0x33333333, 0x44444444]

    for i, val in enumerate(test_values):
        await usb_bfm.send_etherbone_write(base_addr + i * 4, val)

    # Now burst read
    addresses = [base_addr + i * 4 for i in range(len(test_values))]
    values = await usb_bfm.send_etherbone_burst_read(addresses, timeout_cycles=5000)

    assert len(values) == len(test_values), \
        f"Expected {len(test_values)} values, got {len(values)}"

    for i, (expected, actual) in enumerate(zip(test_values, values)):
        assert actual == expected, \
            f"Address 0x{addresses[i]:03X}: expected 0x{expected:08X}, got 0x{actual:08X}"

    dut._log.info(f"Burst read test passed ({len(test_values)} words)")


@cocotb.test()
async def test_burst_write(dut):
    """
    Burst write consecutive addresses.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    base_addr = 0x400
    test_values = [0xAAAAAAAA, 0xBBBBBBBB, 0xCCCCCCCC, 0xDDDDDDDD]

    # Burst write
    await usb_bfm.send_etherbone_burst_write(base_addr, test_values)

    # Read back individually to verify
    for i, expected in enumerate(test_values):
        addr = base_addr + i * 4
        actual = await usb_bfm.send_etherbone_read(addr, timeout_cycles=5000)
        assert actual == expected, \
            f"Address 0x{addr:03X}: expected 0x{expected:08X}, got 0x{actual:08X}"

    dut._log.info(f"Burst write test passed ({len(test_values)} words)")


# =============================================================================
# Stress Tests
# =============================================================================

@cocotb.test()
async def test_rapid_operations(dut):
    """
    Rapid sequential read/write operations.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    NUM_ITERATIONS = 20
    test_addr = 0x500

    for i in range(NUM_ITERATIONS):
        test_value = (i << 24) | (i << 16) | (i << 8) | i
        await usb_bfm.send_etherbone_write(test_addr, test_value)
        readback = await usb_bfm.send_etherbone_read(test_addr, timeout_cycles=5000)
        assert readback == test_value, \
            f"Iteration {i}: wrote 0x{test_value:08X}, got 0x{readback:08X}"

    dut._log.info(f"Rapid operations test passed ({NUM_ITERATIONS} iterations)")


@cocotb.test()
async def test_multiple_id_reads(dut):
    """
    Multiple reads of the ID register to verify consistency.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)

    for i in range(10):
        value = await usb_bfm.send_etherbone_read(ID_ADDRESS, timeout_cycles=5000)
        assert value == ID_VALUE, \
            f"Read {i}: expected 0x{ID_VALUE:08X}, got 0x{value:08X}"

    dut._log.info("Multiple ID reads test passed (10 iterations)")
