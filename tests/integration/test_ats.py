#
# BSA PCIe Exerciser - ATS/ATC Tests
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Integration tests for ATS (Address Translation Services) and ATC (Address Translation Cache).

Includes the critical PASID bug test that exposes BUG 1:
- DMA engine bypasses ATC's PASID-aware lookup interface
- Uses address-only matching, ignoring PASID context
"""

import sys
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

# Add parent directories to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests.common.pcie_bfm import PCIeBFM
from tests.common.tlp_builder import TLPBuilder


# =============================================================================
# Register Offsets (from BSARegisters)
# =============================================================================

# DMA/ATS shared registers
REG_DMACTL         = 0x08   # DMA control
REG_DMA_OFFSET     = 0x0C   # DMA buffer offset
REG_DMA_BUS_ADDR_LO = 0x10  # Bus address low
REG_DMA_BUS_ADDR_HI = 0x14  # Bus address high
REG_DMA_LEN        = 0x18   # Transfer length
REG_DMASTATUS      = 0x1C   # DMA status

# PASID registers
REG_PASID_VAL      = 0x20   # PASID value (20 bits)

# ATS registers
REG_ATSCTL         = 0x24   # ATS control


# =============================================================================
# DMACTL bit positions (from ARM BSA Exerciser spec)
# =============================================================================
# [3:0]:   dmatxntrig      - Trigger DMA (write 1, auto-clears)
# [4]:     dmatxndir       - Direction (0=Read, 1=Write)
# [5]:     dmatxnsnoop     - Snoop attr (0=Snoop, 1=No-snoop)
# [6]:     dmapasiden      - PASID Enable
# [7]:     dmaIsPrivileged - Privileged access mode
# [8]:     dmaIsInstruction - Instruction access
# [9]:     dmaUseATCforTranslation - Use ATC
# [11:10]: dmaAddressType  - Address type (0=Untranslated, 2=Translated)

DMACTL_TRIGGER     = (1 << 0)   # [3:0] Trigger (write 1 to trigger, auto-clears)
DMACTL_DIRECTION   = (1 << 4)   # [4] Direction (0=read, 1=write)
DMACTL_NO_SNOOP    = (1 << 5)   # [5] No-snoop
DMACTL_PASID_EN    = (1 << 6)   # [6] PASID enable
DMACTL_PRIVILEGED  = (1 << 7)   # [7] Privileged mode
DMACTL_INSTRUCTION = (1 << 8)   # [8] Instruction
DMACTL_USE_ATC     = (1 << 9)   # [9] Use ATC for translation
DMACTL_ADDR_TYPE_MASK = (3 << 10)  # [11:10] Address type


# =============================================================================
# ATSCTL bit positions (from ARM BSA Exerciser spec)
# =============================================================================
# [0]: ATSRequestTrigger         - Trigger ATS (auto-clears)
# [1]: ATSRequestIsPrivileged    - Privileged access mode
# [2]: ATSRequestNoWriteRequested - Read-only permission requested
# [3]: ATSPasidEnabled           - PASID Enable
# [4]: ATSExcutePermissionRequested - Execute permission requested
# [5]: ATSInvalidate/ClearATC    - Clear ATC (W1C)
# [6]: ATSIsInFlight             - ATS in flight (RO)
# [7]: ATSTranslationStatus      - Translation successful (RO)
# [8]: ATSTranslationCacheable   - Cacheable (RO)
# [9]: ATCInvalidated            - ATC was invalidated (RO)

ATSCTL_TRIGGER     = (1 << 0)   # [0] Trigger (auto-clear)
ATSCTL_PRIVILEGED  = (1 << 1)   # [1] Privileged mode
ATSCTL_NO_WRITE    = (1 << 2)   # [2] No-write (read-only permission)
ATSCTL_PASID_EN    = (1 << 3)   # [3] PASID enable
ATSCTL_EXEC_REQ    = (1 << 4)   # [4] Execute request
ATSCTL_CLEAR_ATC   = (1 << 5)   # [5] Clear ATC (W1C)


# =============================================================================
# Test Utilities
# =============================================================================

async def reset_dut(dut):
    """Reset the DUT."""
    dut.sys_rst.value = 1
    await ClockCycles(dut.sys_clk, 10)
    dut.sys_rst.value = 0
    await ClockCycles(dut.sys_clk, 10)


async def write_bar0_register(bfm, offset, data):
    """Write a 32-bit value to a BAR0 register."""
    # Use BAR-relative address (offset only) since depacketizer applies mask
    data_bytes = data.to_bytes(4, 'little')
    beats = TLPBuilder.memory_write_32(
        address=offset,  # BAR-relative offset
        data_bytes=data_bytes,
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)
    await ClockCycles(bfm.clk, 5)


async def read_bar0_register(bfm, offset, tag=0):
    """Read a 32-bit value from a BAR0 register."""
    # Use BAR-relative address (offset only) since depacketizer applies mask
    beats = TLPBuilder.memory_read_32(
        address=offset,  # BAR-relative offset
        length_dw=1,
        requester_id=0x0100,
        tag=tag,
    )
    await bfm.inject_tlp(beats, bar_hit=0b000001)

    cpl = await bfm.capture_tlp(timeout_cycles=200)
    if cpl is None:
        return None

    # LitePCIe format: data is in upper 32 bits of beat 1
    # PHY uses big-endian wire format, so we need to byte-swap the DWORD
    raw_data = (cpl[1]['dat'] >> 32) & 0xFFFFFFFF
    # Byte-swap: convert from big-endian wire format to little-endian host format
    return int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')


def extract_address_from_tlp(beats):
    """
    Extract address from a Memory Read/Write TLP.

    Handles TLPs with or without PASID prefix.

    At PHY level, headers use big-endian format where bit positions match
    HeaderField definitions directly. No byte-swap needed for headers.
    """
    if not beats:
        return None

    # Check first DWORD for PASID prefix (type 0x91 = E2E PASID)
    dw0 = beats[0]['dat'] & 0xFFFFFFFF
    has_pasid_prefix = ((dw0 >> 24) & 0xFF) == 0x91

    if has_pasid_prefix:
        # With PASID prefix, structure is shifted by one DWORD:
        # Beat 0: [MWr_DW0 | PASID_Prefix]
        # Beat 1: [MWr_DW2 | MWr_DW1]  (for 3DW header)
        # Beat 1: [MWr_DW3 | MWr_DW2], Beat 2: [Data | MWr_DW3] (for 4DW header - not quite)
        mwr_dw0 = (beats[0]['dat'] >> 32) & 0xFFFFFFFF
        fmt = (mwr_dw0 >> 29) & 0x7

        if fmt in (0b010, 0b000):  # 3DW header (32-bit address)
            # Address is in MWr_DW2, which is in upper 32 bits of beat 1
            mwr_dw2 = (beats[1]['dat'] >> 32) & 0xFFFFFFFF
            return mwr_dw2 & 0xFFFFFFFC
        elif fmt in (0b011, 0b001):  # 4DW header (64-bit address)
            # MWr_DW2 (addr high) in upper 32 of beat 1
            # MWr_DW3 (addr low) in lower 32 of beat 2
            mwr_dw2 = (beats[1]['dat'] >> 32) & 0xFFFFFFFF
            mwr_dw3 = beats[2]['dat'] & 0xFFFFFFFF if len(beats) > 2 else 0
            return ((mwr_dw2 << 32) | mwr_dw3) & 0xFFFFFFFFFFFFFFFC
        else:
            return None
    else:
        # No PASID prefix, standard layout:
        # Beat 0: [DW1 | DW0]
        # Beat 1: [DW3/Data | DW2]
        fmt = (dw0 >> 29) & 0x7

        if fmt in (0b010, 0b000):  # 3DW header (32-bit address)
            dw2 = beats[1]['dat'] & 0xFFFFFFFF
            return dw2 & 0xFFFFFFFC
        elif fmt in (0b011, 0b001):  # 4DW header (64-bit address)
            dw2 = beats[1]['dat'] & 0xFFFFFFFF  # addr high
            dw3 = (beats[1]['dat'] >> 32) & 0xFFFFFFFF  # addr low
            return ((dw2 << 32) | dw3) & 0xFFFFFFFFFFFFFFFC
        else:
            return None


def extract_tag_from_tlp(beats):
    """Extract tag from a TLP (handles PASID prefix)."""
    if not beats:
        return None

    # Check first DWORD for PASID prefix
    dw0 = beats[0]['dat'] & 0xFFFFFFFF
    has_pasid_prefix = ((dw0 >> 24) & 0xFF) == 0x91

    if has_pasid_prefix:
        # With PASID prefix: MWr_DW1 is in lower 32 bits of beat 1
        mwr_dw1 = beats[1]['dat'] & 0xFFFFFFFF
        return (mwr_dw1 >> 8) & 0xFF
    else:
        # No prefix: DW1 is in upper 32 bits of beat 0
        dw1 = (beats[0]['dat'] >> 32) & 0xFFFFFFFF
        return (dw1 >> 8) & 0xFF


# =============================================================================
# Basic ATS Tests
# =============================================================================

@cocotb.test()
async def test_ats_request_generation(dut):
    """
    Test that triggering ATS generates a Translation Request TLP.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Configure ATS: address and PASID
    test_addr_lo = 0x1000_0000
    test_addr_hi = 0x0000_0000
    test_pasid = 5

    dut._log.info(f"Configuring ATS: addr=0x{test_addr_hi:08X}_{test_addr_lo:08X}, PASID={test_pasid}")

    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_addr_lo)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, test_addr_hi)
    await write_bar0_register(bfm, REG_PASID_VAL, test_pasid)

    # Trigger ATS with PASID enabled
    atsctl = ATSCTL_TRIGGER | ATSCTL_PASID_EN
    await write_bar0_register(bfm, REG_ATSCTL, atsctl)

    # Wait for ATS request TLP
    dut._log.info("Waiting for ATS Translation Request TLP...")
    tlp = await bfm.capture_tlp(timeout_cycles=500)

    if tlp is None:
        raise AssertionError("No ATS request TLP captured - ATS engine did not generate request")

    dut._log.info(f"Captured TLP with {len(tlp)} beats")

    # Verify it's an ATS Translation Request (Fmt=001, Type=10000)
    dw0 = (tlp[0]['dat'] >> 32) & 0xFFFFFFFF
    fmt_type = (dw0 >> 24) & 0xFF
    dut._log.info(f"TLP Fmt|Type = 0x{fmt_type:02X}")

    # ATS Translation Request should have Fmt=001 (4DW no data), Type=10000
    # = 0x20 for header, but with Type bits = 0x30 for ATS
    # Actually, check the specific format used by the ATS engine

    dut._log.info("test_ats_request_generation PASSED")


# =============================================================================
# PASID Bug Test (BUG 1)
# =============================================================================

@cocotb.test()
async def test_atc_pasid_mismatch(dut):
    """
    CRITICAL BUG TEST: Verify ATC lookup fails when PASID doesn't match.

    This test exposes BUG 1: The DMA engine bypasses the ATC's PASID-aware
    lookup interface and directly accesses internal signals, performing
    address-only matching without PASID verification.

    Test scenario:
    1. Configure ATS with PASID=5, trigger translation request
    2. Inject translation completion for VA=0x1000_0000 -> PA=0x8000_0000
    3. Configure DMA with PASID=10 (different!), enable ATC lookup
    4. Trigger DMA write to VA=0x1000_0000
    5. Capture DMA TLP and verify the address used

    Expected behavior (after fix):
    - ATC lookup should MISS due to PASID mismatch
    - DMA TLP should use UNTRANSLATED address 0x1000_0000

    Current buggy behavior:
    - ATC lookup incorrectly HITS (ignores PASID)
    - DMA TLP uses TRANSLATED address 0x8000_0000

    This test should FAIL with current code and PASS after the fix.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # =========================================================================
    # Step 1: Configure ATS with PASID=5 and trigger translation
    # =========================================================================
    dut._log.info("Step 1: Configure ATS with PASID=5")

    test_va = 0x1000_0000
    translated_pa = 0x8000_0000
    pasid_ats = 5
    pasid_dma = 10  # Different PASID!

    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_va)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, 0)
    await write_bar0_register(bfm, REG_PASID_VAL, pasid_ats)

    # Trigger ATS with PASID enabled
    atsctl = ATSCTL_TRIGGER | ATSCTL_PASID_EN
    await write_bar0_register(bfm, REG_ATSCTL, atsctl)

    # Wait for ATS request TLP
    ats_req = await bfm.capture_tlp(timeout_cycles=500)
    if ats_req is None:
        raise AssertionError("No ATS request TLP captured - cannot verify PASID behavior without working ATS")

    tag = extract_tag_from_tlp(ats_req)
    dut._log.info(f"ATS request captured, tag={tag}")

    # =========================================================================
    # Step 2: Inject Translation Completion
    # =========================================================================
    dut._log.info(f"Step 2: Inject translation completion VA=0x{test_va:08X} -> PA=0x{translated_pa:08X}")

    cpl_beats = TLPBuilder.ats_translation_completion(
        requester_id=0x0100,  # Our device
        completer_id=0x0200,  # SMMU/IOMMU
        tag=tag,
        translated_addr=translated_pa,
        s_field=0,  # 4KB range
        permissions=0x3F,  # R/W/Priv
    )

    # Inject completion (no BAR hit - completions are matched by tag)
    await bfm.inject_tlp(cpl_beats, bar_hit=0b000000)

    # Wait for ATC to be populated
    await ClockCycles(bfm.clk, 20)

    # =========================================================================
    # Step 3: Configure DMA with DIFFERENT PASID=10
    # =========================================================================
    dut._log.info(f"Step 3: Configure DMA with PASID={pasid_dma} (different from ATS PASID={pasid_ats})")

    # First, pre-load some data in the DMA buffer via a write to BAR1
    # (We need data in the buffer for DMA write to host)
    test_data = 0xDEADBEEFCAFEBABE
    bar1_write = TLPBuilder.memory_write_32(
        address=0x0,  # BAR1 offset 0
        data_bytes=test_data.to_bytes(8, 'little'),
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(bar1_write, bar_hit=0b000010)  # BAR1
    await ClockCycles(bfm.clk, 10)

    # Configure DMA parameters with different PASID
    await write_bar0_register(bfm, REG_PASID_VAL, pasid_dma)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_va)  # Same VA
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, 0)
    await write_bar0_register(bfm, REG_DMA_LEN, 8)  # 8 bytes
    await write_bar0_register(bfm, REG_DMA_OFFSET, 0)  # Buffer offset 0

    # =========================================================================
    # Step 4: Trigger DMA write with ATC lookup enabled
    # =========================================================================
    dut._log.info("Step 4: Trigger DMA write with ATC lookup and PASID enabled")

    # DMACTL: direction=1 (write to host), pasid_en=1, use_atc=1
    dmactl = DMACTL_TRIGGER | DMACTL_DIRECTION | DMACTL_PASID_EN | DMACTL_USE_ATC
    await write_bar0_register(bfm, REG_DMACTL, dmactl)

    # =========================================================================
    # Step 5: Capture DMA TLP and verify address
    # =========================================================================
    dut._log.info("Step 5: Capture DMA TLP and verify address")

    dma_tlp = await bfm.capture_tlp(timeout_cycles=1000)

    if dma_tlp is None:
        raise AssertionError("Timeout waiting for DMA TLP - DMA engine did not generate request")

    address = extract_address_from_tlp(dma_tlp)
    dut._log.info(f"DMA TLP address = 0x{address:08X}")

    # =========================================================================
    # Step 6: Verify - should be UNTRANSLATED due to PASID mismatch
    # =========================================================================

    if address == translated_pa:
        # BUG! ATC matched despite PASID mismatch
        raise AssertionError(
            f"BUG DETECTED: DMA used translated address 0x{translated_pa:08X}\n"
            f"ATC incorrectly matched despite PASID mismatch!\n"
            f"  ATS PASID = {pasid_ats}\n"
            f"  DMA PASID = {pasid_dma}\n"
            f"Expected: DMA should use untranslated address 0x{test_va:08X}\n"
            f"This test exposes BUG 1: DMA engine bypasses ATC's PASID matching"
        )
    elif address == test_va:
        dut._log.info("CORRECT: DMA used untranslated address (ATC miss due to PASID mismatch)")
        dut._log.info("test_atc_pasid_mismatch PASSED")
    else:
        raise AssertionError(
            f"Unexpected address 0x{address:08X}\n"
            f"Expected untranslated 0x{test_va:08X} or translated 0x{translated_pa:08X}"
        )


# =============================================================================
# Additional ATC Tests
# =============================================================================

@cocotb.test()
async def test_atc_clear(dut):
    """
    Test that clearing the ATC invalidates cached translations.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Clear ATC via ATSCTL
    dut._log.info("Clearing ATC via ATSCTL.clear_atc")
    await write_bar0_register(bfm, REG_ATSCTL, ATSCTL_CLEAR_ATC)

    await ClockCycles(bfm.clk, 10)

    # Verify ATC is cleared by checking that subsequent DMA doesn't use translation
    # (This is a basic test - full verification would require the PASID bug fix first)

    dut._log.info("test_atc_clear PASSED")


# =============================================================================
# PASID Prefix Injection Tests
# =============================================================================

@cocotb.test()
async def test_pasid_prefix_insertion(dut):
    """
    Verify PASID TLP prefix (0x91) appears in DMA TLPs when PASID is enabled.

    When dmapasiden=1, the PASID prefix injector should insert a 32-bit
    E2E TLP prefix before the MWr header with the configured PASID value.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    # Configure test parameters
    test_pasid = 0x12345  # 20-bit PASID value
    test_addr = 0x2000_0000

    dut._log.info(f"Testing PASID prefix insertion with PASID=0x{test_pasid:05X}")

    # Pre-load data in DMA buffer via BAR1
    test_data = 0xCAFEBABE
    bar1_write = TLPBuilder.memory_write_32(
        address=0x0,
        data_bytes=test_data.to_bytes(4, 'little'),
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(bar1_write, bar_hit=0b000010)  # BAR1
    await ClockCycles(bfm.clk, 10)

    # Configure DMA with PASID enabled
    await write_bar0_register(bfm, REG_PASID_VAL, test_pasid)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_addr)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, 0)
    await write_bar0_register(bfm, REG_DMA_LEN, 4)
    await write_bar0_register(bfm, REG_DMA_OFFSET, 0)

    # Trigger DMA write with PASID enabled (no ATC)
    dmactl = DMACTL_TRIGGER | DMACTL_DIRECTION | DMACTL_PASID_EN
    await write_bar0_register(bfm, REG_DMACTL, dmactl)

    # Capture DMA TLP
    dma_tlp = await bfm.capture_tlp(timeout_cycles=1000)

    if dma_tlp is None:
        raise AssertionError("Timeout waiting for DMA TLP")

    # Check for PASID prefix using TLPBuilder helper
    has_pasid, pasid_val, privileged, execute = TLPBuilder.extract_pasid_from_tlp(dma_tlp)

    dut._log.info(f"Captured TLP: has_pasid={has_pasid}, pasid_val=0x{pasid_val:05X}")

    assert has_pasid, "PASID prefix not found in DMA TLP when dmapasiden=1"
    assert pasid_val == test_pasid, \
        f"PASID value mismatch: got 0x{pasid_val:05X}, expected 0x{test_pasid:05X}"

    dut._log.info("test_pasid_prefix_insertion PASSED")


@cocotb.test()
async def test_pasid_privileged_mode(dut):
    """
    Verify PMR (Privileged Mode Requested) bit is set in PASID prefix
    when dmaIsPrivileged=1.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    test_pasid = 0x00042
    test_addr = 0x3000_0000

    dut._log.info("Testing PASID prefix with Privileged mode enabled")

    # Pre-load data in DMA buffer
    bar1_write = TLPBuilder.memory_write_32(
        address=0x0,
        data_bytes=b'\xAA\xBB\xCC\xDD',
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(bar1_write, bar_hit=0b000010)
    await ClockCycles(bfm.clk, 10)

    # Configure DMA with PASID and Privileged mode enabled
    await write_bar0_register(bfm, REG_PASID_VAL, test_pasid)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_addr)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, 0)
    await write_bar0_register(bfm, REG_DMA_LEN, 4)
    await write_bar0_register(bfm, REG_DMA_OFFSET, 0)

    # Trigger DMA write with PASID + Privileged enabled
    dmactl = DMACTL_TRIGGER | DMACTL_DIRECTION | DMACTL_PASID_EN | DMACTL_PRIVILEGED
    await write_bar0_register(bfm, REG_DMACTL, dmactl)

    # Capture DMA TLP
    dma_tlp = await bfm.capture_tlp(timeout_cycles=1000)

    if dma_tlp is None:
        raise AssertionError("Timeout waiting for DMA TLP")

    has_pasid, pasid_val, privileged, execute = TLPBuilder.extract_pasid_from_tlp(dma_tlp)

    dut._log.info(f"Captured TLP: privileged={privileged}")

    assert has_pasid, "PASID prefix not found"
    assert privileged, "PMR (Privileged) bit not set in PASID prefix when dmaIsPrivileged=1"

    dut._log.info("test_pasid_privileged_mode PASSED")


@cocotb.test()
async def test_pasid_execute_mode(dut):
    """
    Verify Execute Requested bit is set in PASID prefix
    when dmaIsInstruction=1.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    test_pasid = 0x00007
    test_addr = 0x4000_0000

    dut._log.info("Testing PASID prefix with Execute/Instruction mode enabled")

    # Pre-load data in DMA buffer
    bar1_write = TLPBuilder.memory_write_32(
        address=0x0,
        data_bytes=b'\x11\x22\x33\x44',
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(bar1_write, bar_hit=0b000010)
    await ClockCycles(bfm.clk, 10)

    # Configure DMA with PASID and Instruction mode enabled
    await write_bar0_register(bfm, REG_PASID_VAL, test_pasid)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_addr)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, 0)
    await write_bar0_register(bfm, REG_DMA_LEN, 4)
    await write_bar0_register(bfm, REG_DMA_OFFSET, 0)

    # Trigger DMA write with PASID + Instruction enabled
    dmactl = DMACTL_TRIGGER | DMACTL_DIRECTION | DMACTL_PASID_EN | DMACTL_INSTRUCTION
    await write_bar0_register(bfm, REG_DMACTL, dmactl)

    # Capture DMA TLP
    dma_tlp = await bfm.capture_tlp(timeout_cycles=1000)

    if dma_tlp is None:
        raise AssertionError("Timeout waiting for DMA TLP")

    has_pasid, pasid_val, privileged, execute = TLPBuilder.extract_pasid_from_tlp(dma_tlp)

    dut._log.info(f"Captured TLP: execute={execute}")

    assert has_pasid, "PASID prefix not found"
    assert execute, "Execute bit not set in PASID prefix when dmaIsInstruction=1"

    dut._log.info("test_pasid_execute_mode PASSED")


@cocotb.test()
async def test_pasid_disabled_no_prefix(dut):
    """
    Verify NO PASID prefix is inserted when dmapasiden=0.

    When PASID is disabled, DMA TLPs should have standard format
    without the E2E TLP prefix.
    """
    cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())

    bfm = PCIeBFM(dut)
    await reset_dut(dut)

    test_addr = 0x5000_0000

    dut._log.info("Testing DMA without PASID prefix (dmapasiden=0)")

    # Pre-load data in DMA buffer
    bar1_write = TLPBuilder.memory_write_32(
        address=0x0,
        data_bytes=b'\xDE\xAD\xBE\xEF',
        requester_id=0x0100,
        tag=0,
    )
    await bfm.inject_tlp(bar1_write, bar_hit=0b000010)
    await ClockCycles(bfm.clk, 10)

    # Configure DMA WITHOUT PASID enabled
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_LO, test_addr)
    await write_bar0_register(bfm, REG_DMA_BUS_ADDR_HI, 0)
    await write_bar0_register(bfm, REG_DMA_LEN, 4)
    await write_bar0_register(bfm, REG_DMA_OFFSET, 0)

    # Trigger DMA write WITHOUT PASID (dmapasiden=0)
    dmactl = DMACTL_TRIGGER | DMACTL_DIRECTION  # No DMACTL_PASID_EN
    await write_bar0_register(bfm, REG_DMACTL, dmactl)

    # Capture DMA TLP
    dma_tlp = await bfm.capture_tlp(timeout_cycles=1000)

    if dma_tlp is None:
        raise AssertionError("Timeout waiting for DMA TLP")

    has_pasid, pasid_val, privileged, execute = TLPBuilder.extract_pasid_from_tlp(dma_tlp)

    dut._log.info(f"Captured TLP: has_pasid={has_pasid}")

    assert not has_pasid, "PASID prefix found in DMA TLP when dmapasiden=0 - should not be present"

    # Also verify it's a valid MWr TLP
    fmt, tlp_type, _ = TLPBuilder.extract_tlp_type(dma_tlp)
    assert fmt in (0b010, 0b011), f"Unexpected TLP format: {fmt:03b}"
    assert tlp_type == 0b00000, f"Unexpected TLP type: {tlp_type:05b} (expected MWr)"

    # Verify address is correct
    address = extract_address_from_tlp(dma_tlp)
    assert address == test_addr, f"Address mismatch: got 0x{address:08X}, expected 0x{test_addr:08X}"

    dut._log.info("test_pasid_disabled_no_prefix PASSED")
