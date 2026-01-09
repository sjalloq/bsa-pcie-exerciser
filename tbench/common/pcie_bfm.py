#
# BSA PCIe Exerciser - PCIe Bus Functional Models
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Bus Functional Models for PCIe testing.

Provides BFMs for:
- PHY-level TLP injection and capture (PCIeBFM)
- Request-level TLP handling (TLPRequestSource, TLPCompletionSink)
- DMA TLP handling (DMATLPRequestSink, DMATLPCompletionSource)
"""

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles


# =============================================================================
# PHY-Level BFM (for integration tests)
# =============================================================================

class PCIeBFM:
    """
    Bus Functional Model for PHY stub control.

    Provides methods for injecting and capturing raw TLP beats at the PHY level.
    Used for integration tests where TLPs go through the full depacketizer/packetizer.
    """

    def __init__(self, dut, rx_prefix="phy_rx", tx_prefix="phy_tx"):
        """
        Initialize BFM with DUT reference.

        Args:
            dut: Cocotb DUT reference
            rx_prefix: Prefix for RX signals (testbench -> DUT)
            tx_prefix: Prefix for TX signals (DUT -> testbench)
        """
        self.dut = dut
        self.clk = dut.sys_clk
        self.rx_prefix = rx_prefix
        self.tx_prefix = tx_prefix
        self._get_signals()

    def _get_signals(self):
        """Get signal handles from DUT."""
        rx = self.rx_prefix
        tx = self.tx_prefix

        # RX signals (testbench -> DUT)
        self.rx_valid   = getattr(self.dut, f"{rx}_valid")
        self.rx_ready   = getattr(self.dut, f"{rx}_ready")
        self.rx_first   = getattr(self.dut, f"{rx}_first")
        self.rx_last    = getattr(self.dut, f"{rx}_last")
        self.rx_dat     = getattr(self.dut, f"{rx}_dat")
        self.rx_be      = getattr(self.dut, f"{rx}_be")
        self.rx_bar_hit = getattr(self.dut, f"{rx}_bar_hit")

        # TX signals (DUT -> testbench)
        self.tx_valid   = getattr(self.dut, f"{tx}_valid")
        self.tx_ready   = getattr(self.dut, f"{tx}_ready")
        self.tx_first   = getattr(self.dut, f"{tx}_first")
        self.tx_last    = getattr(self.dut, f"{tx}_last")
        self.tx_dat     = getattr(self.dut, f"{tx}_dat")
        self.tx_be      = getattr(self.dut, f"{tx}_be")

        # Initialize TX ready immediately - always ready to accept completions
        self.tx_ready.value = 1
        # Initialize RX signals to idle
        self.rx_valid.value = 0
        self.rx_first.value = 0
        self.rx_last.value = 0
        self.rx_dat.value = 0
        self.rx_be.value = 0
        self.rx_bar_hit.value = 0

    async def reset(self):
        """Reset the DUT and initialize PHY signals."""
        self.dut.sys_rst.value = 1
        self.rx_valid.value = 0
        self.rx_first.value = 0
        self.rx_last.value = 0
        self.rx_dat.value = 0
        self.rx_be.value = 0
        self.rx_bar_hit.value = 0
        self.tx_ready.value = 1

        await ClockCycles(self.clk, 10)
        self.dut.sys_rst.value = 0
        await ClockCycles(self.clk, 10)

    async def inject_beat(self, dat, be, bar_hit, first, last):
        """
        Inject a single beat into RX path.

        Args:
            dat: 64-bit data value
            be: 8-bit byte enables
            bar_hit: 6-bit BAR hit bitmap
            first: True if first beat of TLP
            last: True if last beat of TLP
        """
        self.rx_valid.value = 1
        self.rx_dat.value = dat
        self.rx_be.value = be
        self.rx_bar_hit.value = bar_hit
        self.rx_first.value = 1 if first else 0
        self.rx_last.value = 1 if last else 0

        while True:
            await RisingEdge(self.clk)
            if self.rx_ready.value:
                break

        self.rx_valid.value = 0
        self.rx_first.value = 0
        self.rx_last.value = 0

    async def capture_beat(self, timeout_cycles=1000):
        """
        Capture a single beat from TX path.

        Args:
            timeout_cycles: Maximum cycles to wait

        Returns:
            Dict with beat data, or None on timeout
        """
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.tx_valid.value and self.tx_ready.value:
                return {
                    'dat': int(self.tx_dat.value),
                    'be': int(self.tx_be.value),
                    'first': int(self.tx_first.value),
                    'last': int(self.tx_last.value),
                }
        return None

    async def inject_tlp(self, beats, bar_hit=0b000001):
        """
        Inject complete TLP (list of beat dicts with 'dat', 'be').

        Args:
            beats: List of dicts with 'dat' and optional 'be' keys
            bar_hit: BAR hit bitmap (default BAR0)
        """
        for i, beat in enumerate(beats):
            await self.inject_beat(
                dat=beat['dat'],
                be=beat.get('be', 0xFF),
                bar_hit=bar_hit,
                first=(i == 0),
                last=(i == len(beats) - 1),
            )

    async def capture_tlp(self, timeout_cycles=1000):
        """
        Capture complete TLP, returns list of beat dicts.

        Args:
            timeout_cycles: Maximum cycles to wait for first beat

        Returns:
            List of beat dicts, or None on timeout
        """
        beats = []
        beat = await self.capture_beat(timeout_cycles)
        if beat is None:
            return None
        beats.append(beat)
        while not beat['last']:
            beat = await self.capture_beat(timeout_cycles)
            if beat is None:
                raise TimeoutError("TLP capture timed out mid-packet")
            beats.append(beat)
        return beats


# =============================================================================
# Request-Level BFMs (from existing DMA tests)
# =============================================================================

class TLPRequestSource:
    """
    Bus Functional Model for sending TLP requests.
    Drives a request sink interface with Memory Read/Write TLPs.
    """

    def __init__(self, dut, prefix="bar1_req_sink"):
        self.dut = dut
        self.prefix = prefix
        self.clk = dut.sys_clk
        self._get_signals()

    def _get_signals(self):
        """Get signal handles from DUT."""
        p = self.prefix
        self.valid = getattr(self.dut, f"{p}_valid")
        self.ready = getattr(self.dut, f"{p}_ready")
        self.first = getattr(self.dut, f"{p}_first")
        self.last = getattr(self.dut, f"{p}_last")
        self.we = getattr(self.dut, f"{p}_we")
        self.adr = getattr(self.dut, f"{p}_adr")
        self.len = getattr(self.dut, f"{p}_len")
        self.req_id = getattr(self.dut, f"{p}_req_id")
        self.tag = getattr(self.dut, f"{p}_tag")
        self.dat = getattr(self.dut, f"{p}_dat")
        self.first_be = getattr(self.dut, f"{p}_first_be")
        self.last_be = getattr(self.dut, f"{p}_last_be")

    async def reset(self):
        """Reset the interface."""
        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0
        self.adr.value = 0
        self.len.value = 0
        self.req_id.value = 0
        self.tag.value = 0
        self.dat.value = 0
        self.first_be.value = 0
        self.last_be.value = 0

    async def write64(self, addr, data, tag=0, req_id=0x0100):
        """Send a 64-bit Memory Write TLP."""
        await RisingEdge(self.clk)

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 1
        self.adr.value = addr
        self.len.value = 2
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = data
        self.first_be.value = 0xF
        self.last_be.value = 0xF

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0

    async def write32(self, addr, data, tag=0, req_id=0x0100):
        """Send a 32-bit Memory Write TLP."""
        if addr & 0x4:
            full_data = data << 32
            first_be = 0x0
            last_be = 0xF
        else:
            full_data = data
            first_be = 0xF
            last_be = 0x0

        await RisingEdge(self.clk)

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 1
        self.adr.value = addr
        self.len.value = 1 if last_be == 0 else 2
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = full_data
        self.first_be.value = first_be
        self.last_be.value = last_be

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.we.value = 0

    async def read(self, addr, length=2, tag=0, req_id=0x0100):
        """Send a Memory Read TLP."""
        await RisingEdge(self.clk)

        if length >= 2:
            first_be = 0xF
            last_be = 0xF
        else:
            first_be = 0xF
            last_be = 0x0

        self.valid.value = 1
        self.first.value = 1
        self.last.value = 1
        self.we.value = 0
        self.adr.value = addr
        self.len.value = length
        self.req_id.value = req_id
        self.tag.value = tag
        self.dat.value = 0
        self.first_be.value = first_be
        self.last_be.value = last_be

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0


class TLPCompletionSink:
    """
    Bus Functional Model for receiving TLP completions.
    """

    def __init__(self, dut, prefix="bar1_cpl_source"):
        self.dut = dut
        self.prefix = prefix
        self.clk = dut.sys_clk
        self._get_signals()
        self.completions = []

    def _get_signals(self):
        """Get signal handles from DUT."""
        p = self.prefix
        self.valid = getattr(self.dut, f"{p}_valid")
        self.ready = getattr(self.dut, f"{p}_ready")
        self.first = getattr(self.dut, f"{p}_first")
        self.last = getattr(self.dut, f"{p}_last")
        self.dat = getattr(self.dut, f"{p}_dat")
        self.tag = getattr(self.dut, f"{p}_tag")
        self.err = getattr(self.dut, f"{p}_err")

    async def reset(self):
        """Reset the interface."""
        self.ready.value = 1
        self.completions = []

    async def wait_completion(self, timeout_cycles=100):
        """Wait for a completion."""
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.valid.value and self.ready.value:
                cpl = {
                    'data': int(self.dat.value),
                    'tag': int(self.tag.value),
                    'err': int(self.err.value),
                }
                self.completions.append(cpl)
                return cpl
        return None


# =============================================================================
# DMA TLP BFMs (from existing DMA tests)
# =============================================================================

class DMATLPRequestSink:
    """
    Bus Functional Model for capturing DMA engine's TLP requests.
    Monitors Memory Read and Write TLPs from the DMA engine.
    """

    def __init__(self, dut, prefix="tlp_req_source"):
        self.dut = dut
        self.prefix = prefix
        self.clk = dut.sys_clk
        self._get_signals()
        self.requests = []

    def _get_signals(self):
        """Get signal handles from DUT."""
        p = self.prefix
        self.valid = getattr(self.dut, f"{p}_valid")
        self.ready = getattr(self.dut, f"{p}_ready")
        self.first = getattr(self.dut, f"{p}_first")
        self.last = getattr(self.dut, f"{p}_last")
        self.we = getattr(self.dut, f"{p}_we")
        self.adr = getattr(self.dut, f"{p}_adr")
        self.len = getattr(self.dut, f"{p}_len")
        self.dat = getattr(self.dut, f"{p}_dat")
        self.attr = getattr(self.dut, f"{p}_attr")
        self.at = getattr(self.dut, f"{p}_at")
        self.tag = getattr(self.dut, f"{p}_tag")

    async def reset(self):
        """Reset the interface - always ready to accept."""
        self.ready.value = 1
        self.requests = []

    async def wait_request(self, timeout_cycles=100):
        """Wait for a TLP request from DMA engine."""
        for _ in range(timeout_cycles):
            await RisingEdge(self.clk)
            if self.valid.value and self.ready.value:
                req = {
                    'we': int(self.we.value),
                    'addr': int(self.adr.value),
                    'len': int(self.len.value),
                    'data': int(self.dat.value),
                    'attr': int(self.attr.value),
                    'at': int(self.at.value),
                    'tag': int(self.tag.value),
                    'first': int(self.first.value),
                    'last': int(self.last.value),
                }
                self.requests.append(req)
                return req
        return None


class DMATLPCompletionSource:
    """
    Bus Functional Model for sending read completions to DMA engine.
    """

    def __init__(self, dut, prefix="tlp_cpl_sink"):
        self.dut = dut
        self.prefix = prefix
        self.clk = dut.sys_clk
        self._get_signals()

    def _get_signals(self):
        """Get signal handles from DUT."""
        p = self.prefix
        self.valid = getattr(self.dut, f"{p}_valid")
        self.ready = getattr(self.dut, f"{p}_ready")
        self.first = getattr(self.dut, f"{p}_first")
        self.last = getattr(self.dut, f"{p}_last")
        self.dat = getattr(self.dut, f"{p}_dat")
        self.err = getattr(self.dut, f"{p}_err")
        self.end = getattr(self.dut, f"{p}_end")
        self.tag = getattr(self.dut, f"{p}_tag")
        self.len = getattr(self.dut, f"{p}_len")

    async def reset(self):
        """Reset the interface."""
        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.dat.value = 0
        self.err.value = 0
        self.end.value = 0
        self.tag.value = 0
        self.len.value = 0

    async def send_completion(self, data, tag, length=2, is_first=True, is_last=True,
                              is_end=True, error=False):
        """Send a completion TLP to DMA engine."""
        await RisingEdge(self.clk)

        self.valid.value = 1
        self.first.value = 1 if is_first else 0
        self.last.value = 1 if is_last else 0
        self.dat.value = data
        self.err.value = 1 if error else 0
        self.end.value = 1 if is_end else 0
        self.tag.value = tag
        self.len.value = length

        while True:
            await RisingEdge(self.clk)
            if self.ready.value:
                break

        self.valid.value = 0
        self.first.value = 0
        self.last.value = 0
        self.end.value = 0
