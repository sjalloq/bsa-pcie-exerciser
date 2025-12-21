#
# LitePCIe Multi-BAR Endpoint
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# PCIe endpoint with multi-BAR routing support using bar_hit field.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import *
from litepcie.core.crossbar import LitePCIeCrossbar
from litepcie.tlp.depacketizer import LitePCIeTLPDepacketizer
from litepcie.tlp.packetizer import LitePCIeTLPPacketizer

from .bar_routing import (
    LitePCIeBARDispatcher,
    LitePCIeCompletionArbiter,
    LitePCIeMasterArbiter,
    LitePCIeStubBARHandler,
)


class LitePCIeMultiBAREndpoint(LiteXModule):
    """
    PCIe Endpoint with multi-BAR routing.

    Routes incoming requests to per-BAR crossbars based on the bar_hit field
    from the PHY. Each BAR gets its own crossbar for independent frontend
    attachment.

    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance.

    endianness : str
        Endianness for TLP processing ("big" or "little").

    address_width : int
        Address width for crossbar ports.

    max_pending_requests : int
        Maximum outstanding requests per crossbar.

    bar_enables : dict
        Which BARs to create crossbars for. Example: {0: True, 1: True, 2: False}
        BARs not in dict or set to False get stub handlers.

    bar_handlers : dict
        Optional custom handlers for specific BARs. Example: {1: my_handler}
        Handlers must have req_sink (Endpoint) and cpl_source (Endpoint) attributes.
        If a handler is provided for a BAR, it takes precedence over bar_enables.
    """

    def __init__(self, phy,
                 endianness="big",
                 address_width=32,
                 max_pending_requests=4,
                 bar_enables=None,
                 bar_handlers=None):
        
        self.phy        = phy
        self.data_width = phy.data_width

        # Default: only BAR0 enabled
        if bar_enables is None:
            bar_enables = {0: True}

        # Default: no custom handlers
        if bar_handlers is None:
            bar_handlers = {}
        
        # =====================================================================
        # TLP Depacketizer / Packetizer
        # =====================================================================
        
        self.depacketizer = LitePCIeTLPDepacketizer(
            data_width   = phy.data_width,
            endianness   = endianness,
            address_mask = phy.bar0_mask,
            capabilities = ["REQUEST", "COMPLETION"],
        )
        
        self.packetizer = LitePCIeTLPPacketizer(
            data_width    = phy.data_width,
            endianness    = endianness,
            address_width = address_width,
            capabilities  = ["REQUEST", "COMPLETION"],
        )
        
        # Connect PHY
        self.comb += [
            phy.source.connect(self.depacketizer.sink),
            self.packetizer.source.connect(phy.sink),
        ]
        
        # =====================================================================
        # Per-BAR Crossbars and Handlers
        # =====================================================================
        
        self.crossbars = {}
        self.bar_handlers = {}
        
        # Create request sinks for dispatcher
        bar_req_sinks = {}
        # Create completion sources for arbiter
        bar_cpl_sources = {}
        # Create master request sources for arbiter
        bar_master_sources = {}
        # Create master completion sinks (for routing completions back)
        bar_master_sinks = {}
        
        for bar_num in range(6):
            if bar_num in bar_handlers:
                # Use custom handler for this BAR
                handler = bar_handlers[bar_num]
                setattr(self, f"bar{bar_num}_handler", handler)
                self.bar_handlers[bar_num] = handler

                bar_req_sinks[bar_num]   = handler.req_sink
                bar_cpl_sources[bar_num] = handler.cpl_source
                # Custom handlers don't have master ports (no DMA initiation)
                # Use dummy signals for master arbiter compatibility
                dummy_req = stream.Endpoint(request_layout(phy.data_width))
                dummy_cpl = stream.Endpoint(completion_layout(phy.data_width))
                self.comb += dummy_req.valid.eq(0)
                self.comb += dummy_cpl.ready.eq(1)
                bar_master_sources[bar_num] = dummy_req
                bar_master_sinks[bar_num]   = dummy_cpl

            elif bar_enables.get(bar_num, False):
                # Create full crossbar for this BAR
                crossbar = LitePCIeCrossbar(
                    data_width           = phy.data_width,
                    address_width        = address_width,
                    max_pending_requests = max_pending_requests,
                    cmp_bufs_buffered    = True,
                )
                setattr(self, f"bar{bar_num}_crossbar", crossbar)
                self.crossbars[bar_num] = crossbar

                bar_req_sinks[bar_num]      = crossbar.phy_slave.sink
                bar_cpl_sources[bar_num]    = crossbar.phy_slave.source
                bar_master_sources[bar_num] = crossbar.phy_master.source
                bar_master_sinks[bar_num]   = crossbar.phy_master.sink

            else:
                # Create stub handler
                stub = LitePCIeStubBARHandler(
                    data_width = phy.data_width,
                    bar_num    = bar_num,
                    return_ur  = True,  # Return UR for read requests
                )
                setattr(self, f"bar{bar_num}_stub", stub)
                self.bar_handlers[bar_num] = stub

                bar_req_sinks[bar_num]      = stub.req_sink
                bar_cpl_sources[bar_num]    = stub.cpl_source
                bar_master_sources[bar_num] = stub.req_source
                bar_master_sinks[bar_num]   = stub.cpl_sink
        
        # =====================================================================
        # Request Dispatcher (depacketizer -> BARs)
        # =====================================================================
        
        self.bar_dispatcher = LitePCIeBARDispatcher(
            source     = self.depacketizer.req_source,
            bar_sinks  = bar_req_sinks,
            default_bar = 0,  # Route unknown to BAR0
        )
        
        # =====================================================================
        # Completion Arbiter (BARs -> packetizer)
        # =====================================================================
        
        self.cpl_arbiter = LitePCIeCompletionArbiter(
            bar_sources = bar_cpl_sources,
            sink        = self.packetizer.cmp_sink,
        )
        
        # =====================================================================
        # Master Request Arbiter (BARs -> packetizer, for DMA/MSI)
        # =====================================================================
        
        # Only include BARs that have real crossbars (might do DMA)
        active_master_sources = {
            bar_num: src 
            for bar_num, src in bar_master_sources.items() 
            if bar_num in self.crossbars
        }
        
        self.master_arbiter = LitePCIeMasterArbiter(
            bar_sources = active_master_sources,
            sink        = self.packetizer.req_sink,
        )
        
        # =====================================================================
        # Master Completion Routing (depacketizer -> BARs, for DMA completions)
        # =====================================================================
        
        # Completions need to go to the right BAR based on channel.
        # Broadcast to all master sinks - each crossbar filters by channel internally.
        # Note: We can't use .connect() multiple times, so use explicit signals.
        
        active_master_sinks = {
            bar_num: sink
            for bar_num, sink in bar_master_sinks.items()
            if bar_num in self.crossbars
        }
        
        cmp_source = self.depacketizer.cmp_source
        
        for bar_num, sink in active_master_sinks.items():
            self.comb += [
                sink.valid.eq(cmp_source.valid),
                sink.first.eq(cmp_source.first),
                sink.last.eq(cmp_source.last),
                sink.dat.eq(cmp_source.dat),
                sink.len.eq(cmp_source.len),
                sink.err.eq(cmp_source.err),
                sink.end.eq(cmp_source.end),
                sink.tag.eq(cmp_source.tag),
                sink.adr.eq(cmp_source.adr),
                sink.req_id.eq(cmp_source.req_id),
                sink.cmp_id.eq(cmp_source.cmp_id),
            ]
        
        # Ready when ANY sink accepts (crossbar handles channel filtering)
        if len(active_master_sinks) > 0:
            self.comb += cmp_source.ready.eq(
                reduce(or_, [sink.ready for sink in active_master_sinks.values()])
            )
        
        # =====================================================================
        # Convenience: expose BAR0 crossbar as 'crossbar' for compatibility
        # =====================================================================

        if 0 in self.crossbars:
            self.crossbar = self.crossbars[0]

        # =====================================================================
        # Expose request source for transaction monitoring
        # =====================================================================

        # The req_source from depacketizer can be tapped for transaction
        # monitoring. This is exposed so the top-level can wire a monitor.
        self.req_source = self.depacketizer.req_source


class LitePCIeBAREndpoint(LiteXModule):
    """
    Lightweight wrapper providing endpoint-like interface for a single BAR.
    
    Used when you need to attach standard LitePCIe frontend components
    (like LitePCIeWishboneBridge) to a specific BAR's crossbar.
    
    Parameters
    ----------
    crossbar : LitePCIeCrossbar
        The crossbar for this BAR.
        
    phy : S7PCIEPHY
        PHY instance (for phy.id used in completions).
    """
    
    def __init__(self, crossbar, phy):
        self.crossbar   = crossbar
        self.phy        = phy
        self.data_width = phy.data_width
