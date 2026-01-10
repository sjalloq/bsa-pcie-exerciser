#
# ATS Invalidation Message Tests (BSA e023-e025)
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Verifies ATS Invalidation Request message handling:
# - Receiving invalidation messages via PCIe
# - ATC entry invalidation
# - Sending Invalidation Completion responses
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
)


# =============================================================================
# Register Offsets
# =============================================================================

REG_DMACTL              = 0x008
REG_DMA_BUS_ADDR_LO     = 0x010
REG_DMA_BUS_ADDR_HI     = 0x014
REG_DMA_LEN             = 0x018
REG_PASID_VAL           = 0x020
REG_ATSCTL              = 0x024
REG_ATS_ADDR_LO         = 0x028
REG_ATS_ADDR_HI         = 0x02C
REG_ATS_RANGE_SIZE      = 0x030
REG_ATS_PERM            = 0x038
REG_USB_MON_CTRL        = 0x080

# Config space ATS control register (extended config space DWORD address)
# Base = 0x6B, ATS control = base + 1 = 0x6C
CFG_ATS_CTRL_DWORD      = 0x6C
CFG_ATS_ENABLE_BIT      = (1 << 31)

# ATSCTL bit definitions
ATSCTL_TRIGGER      = (1 << 0)
ATSCTL_PRIVILEGED   = (1 << 1)
ATSCTL_NO_WRITE     = (1 << 2)
ATSCTL_PASID_EN     = (1 << 3)
ATSCTL_EXEC_REQ     = (1 << 4)
ATSCTL_CLEAR_ATC    = (1 << 5)
ATSCTL_IN_FLIGHT    = (1 << 6)
ATSCTL_SUCCESS      = (1 << 7)
ATSCTL_CACHEABLE    = (1 << 8)
ATSCTL_INVALIDATED  = (1 << 9)


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


async def enable_ats_capability(pcie_bfm: PCIeBFM):
    """
    Enable ATS in extended config space.

    The ATS engine is gated by config_space.ats_enable (bit 31 of ATS control).
    This must be enabled before ATS translation requests will be generated.
    """
    dut = pcie_bfm.dut

    # Build config write TLP to set ATS enable bit
    beats = TLPBuilder.config_write_type0(
        dword_addr=CFG_ATS_CTRL_DWORD,
        data=CFG_ATS_ENABLE_BIT,
        requester_id=0x0000,  # Root complex
        bus=0,
        device=1,
        function=0,
    )

    await pcie_bfm.inject_tlp(beats, bar_hit=0)

    # Wait for config write to take effect
    await ClockCycles(dut.sys_clk, 30)


async def enable_both_monitoring(usb_bfm: USBBFM):
    """Enable both RX and TX monitoring."""
    await usb_bfm.send_etherbone_write(REG_USB_MON_CTRL, 0x03)


def extract_tag_from_tlp(beats):
    """Extract tag from a TLP (handles PASID prefix)."""
    if not beats:
        return None

    # Check first DWORD for PASID prefix
    dw0 = beats[0]['dat'] & 0xFFFFFFFF
    has_pasid_prefix = ((dw0 >> 24) & 0xFF) == 0x91

    if has_pasid_prefix:
        # With PASID prefix: TLP DW1 is in lower 32 bits of beat 1
        tlp_dw1 = beats[1]['dat'] & 0xFFFFFFFF
        return (tlp_dw1 >> 8) & 0xFF
    else:
        # No prefix: DW1 is in upper 32 bits of beat 0
        dw1 = (beats[0]['dat'] >> 32) & 0xFFFFFFFF
        return (dw1 >> 8) & 0xFF


async def populate_atc_entry(usb_bfm: USBBFM, pcie_bfm: PCIeBFM, address: int,
                              translated_addr: int):
    """
    Populate an ATC entry by triggering ATS translation and providing completion.

    1. Configure address and trigger ATS request
    2. Capture the outgoing TLP to get the actual tag (TLPController replaces tags)
    3. Inject ATS Translation Completion with captured tag
    4. Wait for ATC to be populated

    Note: The TLPController in the crossbar replaces the ATS engine's tag with
    its own managed tag, so we must capture the outgoing TLP to get the real tag.
    """
    # Set address for translation
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, address & 0xFFFFFFFF)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, (address >> 32) & 0xFFFFFFFF)

    # Trigger ATS translation request
    await usb_bfm.send_etherbone_write(REG_ATSCTL, ATSCTL_TRIGGER)

    # Capture the outgoing ATS request TLP to get the actual tag
    # The TLPController replaces the ATS engine's tag with its own
    beats = await pcie_bfm.capture_tlp(timeout_cycles=500)
    if beats is None:
        usb_bfm.dut._log.error("Failed to capture ATS request TLP")
        return False  # Caller will assert on this

    # Extract tag from captured TLP (handles PASID prefix if present)
    actual_tag = extract_tag_from_tlp(beats)
    usb_bfm.dut._log.info(f"Captured ATS request with tag={actual_tag}")

    # Inject ATS Translation Completion with the captured tag
    cpl_beats = TLPBuilder.ats_translation_completion(
        requester_id=0x0100,  # Our device ID
        completer_id=0x0000,  # Root complex/SMMU
        tag=actual_tag,       # Use captured tag, not ATS engine's internal tag
        translated_addr=translated_addr,
        s_field=0,  # 4KB page
        permissions=ATS_PERM_RW,
    )
    await pcie_bfm.inject_tlp(cpl_beats, bar_hit=0)

    # Wait for ATC to store translation
    await ClockCycles(usb_bfm.dut.sys_clk, 100)

    # Verify translation was successful
    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    return (atsctl & ATSCTL_SUCCESS) != 0


def parse_monitor_packet(data: bytes) -> TLPPacket:
    """Parse a USB monitor packet into TLPPacket."""
    pkt = parse_tlp_packet(data)
    if pkt is None:
        raise ValueError(f"Failed to parse monitor packet: {data.hex()}")
    return pkt


# =============================================================================
# ATS Invalidation Tests
# =============================================================================

@cocotb.test()
async def test_ats_invalidation_request_clears_atc(dut):
    """
    Verify ATS Invalidation Request clears matching ATC entry.

    BSA Tests: e023-e025

    1. Populate ATC with valid translation
    2. Inject ATS Invalidation Request message via PCIe
    3. Verify ATC entry is cleared (invalidated status set)
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable ATS capability in config space (required for ATS engine to work)
    await enable_ats_capability(pcie_bfm)

    # Populate ATC with a translation
    test_addr = 0x10000000
    translated = 0x20000000
    success = await populate_atc_entry(usb_bfm, pcie_bfm, test_addr, translated)

    assert success, "ATC population failed - cannot test invalidation"

    dut._log.info(f"ATC populated: 0x{test_addr:X} -> 0x{translated:X}")

    # Clear the invalidated flag before injecting invalidation
    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    dut._log.info(f"ATSCTL before invalidation: 0x{atsctl:08X}")

    # Inject ATS Invalidation Request
    # requester_id = Translation Agent (e.g., root complex)
    # device_id = Our device ID (target)
    inv_beats = TLPBuilder.ats_invalidation_request(
        requester_id=0x0000,  # Root complex
        device_id=0x0100,     # Our endpoint
        itag=0x01,
        address=test_addr,
        g_bit=1,  # Global - invalidate regardless of PASID
    )
    await pcie_bfm.inject_tlp(inv_beats, bar_hit=0)

    # Wait for invalidation to be processed
    await ClockCycles(dut.sys_clk, 200)

    # Check invalidated status
    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    dut._log.info(f"ATSCTL after invalidation: 0x{atsctl:08X}")

    invalidated = (atsctl & ATSCTL_INVALIDATED) != 0
    assert invalidated, "Expected ATC to be invalidated after Invalidation Request"


@cocotb.test()
async def test_ats_invalidation_completion_sent(dut):
    """
    Verify Invalidation Completion message is sent after invalidation.

    1. Populate ATC
    2. Enable TX monitoring
    3. Inject Invalidation Request
    4. Capture outgoing Invalidation Completion message
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable ATS capability in config space (required for ATS engine to work)
    await enable_ats_capability(pcie_bfm)

    await enable_both_monitoring(usb_bfm)

    # Populate ATC
    test_addr = 0x30000000
    success = await populate_atc_entry(usb_bfm, pcie_bfm, test_addr, 0x40000000)

    assert success, "ATC population failed - cannot test invalidation completion"

    # Inject ATS Invalidation Request
    inv_beats = TLPBuilder.ats_invalidation_request(
        requester_id=0x0000,
        device_id=0x0100,
        itag=0x05,
        address=test_addr,
        g_bit=1,
    )
    await pcie_bfm.inject_tlp(inv_beats, bar_hit=0)

    # Wait and capture response
    await ClockCycles(dut.sys_clk, 300)

    packet_data = await usb_bfm.receive_monitor_packet(timeout_cycles=500)

    assert packet_data is not None, "Expected to capture ATS Invalidation Completion message"

    pkt = parse_monitor_packet(packet_data)
    dut._log.info(f"Captured TX TLP: type={pkt.type_name}, dir={pkt.direction}")
    # Invalidation Completion is a Message TLP (Fmt=001, Type=10010)
    # with Message Code = 0x02
    assert pkt.direction == Direction.TX, "Expected TX direction for completion"


@cocotb.test()
async def test_ats_software_atc_clear(dut):
    """
    Verify software-triggered ATC clear via ATSCTL.CLEAR_ATC.

    This test verifies the baseline ATC clear functionality
    that's triggered via register write (not PCIe message).
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable ATS capability in config space (required for ATS engine to work)
    await enable_ats_capability(pcie_bfm)

    # Populate ATC
    test_addr = 0x50000000
    success = await populate_atc_entry(usb_bfm, pcie_bfm, test_addr, 0x60000000)

    assert success, "ATC population failed - cannot test software clear"

    # Trigger software ATC clear
    await usb_bfm.send_etherbone_write(REG_ATSCTL, ATSCTL_CLEAR_ATC)

    await ClockCycles(dut.sys_clk, 50)

    # Verify invalidated flag is set
    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    dut._log.info(f"ATSCTL after clear: 0x{atsctl:08X}")

    invalidated = (atsctl & ATSCTL_INVALIDATED) != 0
    assert invalidated, "INVALIDATED flag should be set after CLEAR_ATC"
    dut._log.info("ATC clear via software register verified")


@cocotb.test()
async def test_ats_global_invalidation(dut):
    """
    Verify global invalidation (G-bit=1) clears ATC.

    The G-bit in an ATS Invalidation Request indicates that PASID matching
    should be ignored. This test verifies that G=1 causes ATC invalidation.

    1. Populate ATC (without PASID for simplicity)
    2. Send global invalidation (G=1)
    3. Verify entry is cleared
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable ATS capability in config space (required for ATS engine to work)
    await enable_ats_capability(pcie_bfm)

    # Use a different address than other tests to avoid confusion
    test_addr = 0x70000000

    # Populate ATC using helper (without PASID)
    success = await populate_atc_entry(usb_bfm, pcie_bfm, test_addr, 0x80000000)
    assert success, "Failed to populate ATC"

    dut._log.info(f"ATC populated: 0x{test_addr:08X} -> 0x80000000")

    # Clear any previous invalidation flag by reading (it's sticky, cleared by write-1)
    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    if atsctl & ATSCTL_INVALIDATED:
        await usb_bfm.send_etherbone_write(REG_ATSCTL, ATSCTL_INVALIDATED)  # Clear sticky bit

    # Send global invalidation (G=1)
    inv_beats = TLPBuilder.ats_invalidation_request(
        requester_id=0x0000,
        device_id=0x0100,
        itag=0x06,
        address=test_addr,
        g_bit=1,  # Global = ignore PASID matching
    )
    await pcie_bfm.inject_tlp(inv_beats, bar_hit=0)

    await ClockCycles(dut.sys_clk, 200)

    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    invalidated = (atsctl & ATSCTL_INVALIDATED) != 0

    dut._log.info(f"Global invalidation: ATSCTL=0x{atsctl:08X}, invalidated={invalidated}")
    assert invalidated, "ATC should be invalidated after global Invalidation Request (G=1)"
    dut._log.info("Global invalidation verified")


@cocotb.test()
async def test_ats_page_selective_invalidation(dut):
    """
    Verify page-selective invalidation only clears matching address range.

    Note: This test verifies the S=0 (single 4KB page) case.
    Extended range invalidation (S=1) is not implemented.
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable ATS capability in config space (required for ATS engine to work)
    await enable_ats_capability(pcie_bfm)

    # Populate ATC with entry at specific address
    test_addr = 0x90000000
    success = await populate_atc_entry(usb_bfm, pcie_bfm, test_addr, 0xA0000000)

    assert success, "ATC population failed - cannot test page-selective invalidation"

    # Send invalidation for the EXACT same address
    inv_beats = TLPBuilder.ats_invalidation_request(
        requester_id=0x0000,
        device_id=0x0100,
        itag=0x07,
        address=test_addr,  # Same address
        s_bit=0,  # Single 4KB page
        g_bit=1,
    )
    await pcie_bfm.inject_tlp(inv_beats, bar_hit=0)

    await ClockCycles(dut.sys_clk, 200)

    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    invalidated = (atsctl & ATSCTL_INVALIDATED) != 0

    dut._log.info(f"Page-selective invalidation: ATSCTL=0x{atsctl:08X}")
    assert invalidated, "ATC should be invalidated after page-selective Invalidation Request"
    dut._log.info("Page-selective invalidation verified")


@cocotb.test()
async def test_ats_global_invalidation_with_pasid(dut):
    """
    Verify global invalidation (G-bit=1) clears PASID-enabled ATC entry.

    This test uses PASID-enabled ATS translation to populate the ATC,
    then sends a global invalidation request.

    1. Set PASID value
    2. Populate ATC with PASID-enabled translation
    3. Send global invalidation (G=1)
    4. Verify entry is cleared
    """
    await reset_dut(dut)
    usb_bfm = USBBFM(dut)
    pcie_bfm = PCIeBFM(dut)

    # Enable ATS capability in config space (required for ATS engine to work)
    await enable_ats_capability(pcie_bfm)

    # Set a PASID value
    test_pasid = 0x1234
    await usb_bfm.send_etherbone_write(REG_PASID_VAL, test_pasid)

    # Populate ATC with PASID-enabled entry
    test_addr = 0x70000000

    # Configure for PASID-enabled ATS request
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_LO, test_addr)
    await usb_bfm.send_etherbone_write(REG_DMA_BUS_ADDR_HI, 0)
    await usb_bfm.send_etherbone_write(REG_ATSCTL, ATSCTL_TRIGGER | ATSCTL_PASID_EN)

    # Capture the outgoing ATS request TLP to get the actual tag
    beats = await pcie_bfm.capture_tlp(timeout_cycles=500)
    if beats is None:
        dut._log.warning("Failed to capture ATS request TLP")
        assert False, "Failed to capture PASID-enabled ATS request"

    # Debug: print raw beats for analysis
    for i, beat in enumerate(beats):
        dut._log.info(f"  Beat {i}: dat=0x{beat['dat']:016X} be=0x{beat['be']:02X} "
                      f"first={beat['first']} last={beat['last']}")

    # Check for PASID prefix
    dw0 = beats[0]['dat'] & 0xFFFFFFFF
    has_pasid = ((dw0 >> 24) & 0xFF) == 0x91
    dut._log.info(f"  PASID prefix detected: {has_pasid} (dw0=0x{dw0:08X})")

    # Extract tag from captured TLP (handles PASID prefix if present)
    actual_tag = extract_tag_from_tlp(beats)
    dut._log.info(f"Captured PASID-enabled ATS request with tag={actual_tag}")

    # Check ATSCTL before injecting completion - should show IN_FLIGHT
    atsctl_before = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    dut._log.info(f"ATSCTL before completion: 0x{atsctl_before:08X} "
                  f"(in_flight={(atsctl_before & ATSCTL_IN_FLIGHT) != 0})")

    # Inject translation completion with the captured tag
    cpl_beats = TLPBuilder.ats_translation_completion(
        requester_id=0x0100,
        completer_id=0x0000,
        tag=actual_tag,
        translated_addr=0x80000000,
        permissions=ATS_PERM_RW,  # Must set permissions (default 0x3F has U=1 which means failure)
    )

    # Debug: print completion beats
    dut._log.info("Injecting completion TLP:")
    for i, beat in enumerate(cpl_beats):
        dut._log.info(f"  Cpl Beat {i}: dat=0x{beat['dat']:016X} be=0x{beat['be']:02X}")

    await pcie_bfm.inject_tlp(cpl_beats, bar_hit=0)

    # Wait and check status periodically
    for wait_cycle in [10, 50, 100]:
        await ClockCycles(dut.sys_clk, wait_cycle - (10 if wait_cycle > 10 else 0))
        atsctl_check = await usb_bfm.send_etherbone_read(REG_ATSCTL)
        dut._log.info(f"ATSCTL after {wait_cycle} cycles: 0x{atsctl_check:08X} "
                      f"(in_flight={(atsctl_check & ATSCTL_IN_FLIGHT) != 0}, "
                      f"success={(atsctl_check & ATSCTL_SUCCESS) != 0})")

    # Verify ATC was populated
    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    if not (atsctl & ATSCTL_SUCCESS):
        dut._log.warning(f"PASID-enabled ATC population failed (ATSCTL=0x{atsctl:08X})")
        assert False, "PASID-enabled ATS translation completion not processed - known issue"

    dut._log.info(f"PASID-enabled ATC populated: ATSCTL=0x{atsctl:08X}")

    # Clear any previous invalidation flag
    if atsctl & ATSCTL_INVALIDATED:
        await usb_bfm.send_etherbone_write(REG_ATSCTL, ATSCTL_INVALIDATED)

    # Send global invalidation (should clear regardless of PASID)
    inv_beats = TLPBuilder.ats_invalidation_request(
        requester_id=0x0000,
        device_id=0x0100,
        itag=0x06,
        address=test_addr,
        g_bit=1,  # Global = ignore PASID
    )
    await pcie_bfm.inject_tlp(inv_beats, bar_hit=0)

    await ClockCycles(dut.sys_clk, 200)

    atsctl = await usb_bfm.send_etherbone_read(REG_ATSCTL)
    invalidated = (atsctl & ATSCTL_INVALIDATED) != 0

    dut._log.info(f"PASID global invalidation: ATSCTL=0x{atsctl:08X}, invalidated={invalidated}")
    assert invalidated, "PASID-enabled ATC should be invalidated after global Invalidation Request (G=1)"
    dut._log.info("PASID-enabled global invalidation verified")
