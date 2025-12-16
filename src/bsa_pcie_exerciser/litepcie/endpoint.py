#
# LitePCIe BAR Endpoint
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Lightweight endpoint wrapper for single-BAR routing in multi-BAR designs.
# Provides the same interface as LitePCIeEndpoint, allowing reuse of existing
# LitePCIe frontend components (WishboneBridge, MSI-X, etc.)
#

from migen import *
from functools import reduce
from operator import or_

from litex.gen import *

from litepcie.core.crossbar import LitePCIeCrossbar

# LitePCIeBAREndpoint ------------------------------------------------------------------------------

class LitePCIeBAREndpoint(LiteXModule):
    """
    Lightweight endpoint for a single BAR.
    
    Wraps a LitePCIeCrossbar and provides the same interface that existing
    LitePCIe frontend components expect from LitePCIeEndpoint.
    
    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance. Used to access phy.id for completion routing.
        
    data_width : int
        Data width in bits (must match PHY, typically 64 for x1).
        
    address_width : int
        Address width for the BAR (default 32).
        
    max_pending_requests : int
        Maximum outstanding read requests (default 4).
        
    bar_num : int
        BAR number (0-5) for identification/debugging.
    """
    
    def __init__(self, phy, data_width, address_width=32, max_pending_requests=4, bar_num=0):
        self.phy      = phy
        self.bar_num  = bar_num
        
        # Create crossbar for this BAR
        self.crossbar = LitePCIeCrossbar(
            data_width           = data_width,
            address_width        = address_width,
            max_pending_requests = max_pending_requests,
            cmp_bufs_buffered    = True,
        )
        
        # Expose PHY-side interfaces for connection to dispatcher/arbiters
        # Slave path: Host -> FPGA (requests come in, completions go out)
        self.req_sink   = self.crossbar.phy_slave.sink    # Incoming requests
        self.cpl_source = self.crossbar.phy_slave.source  # Outgoing completions
        
        # Master path: FPGA -> Host (requests go out, completions come in)
        self.req_source = self.crossbar.phy_master.source # Outgoing requests (DMA/MSI)
        self.cpl_sink   = self.crossbar.phy_master.sink   # Incoming completions


# LitePCIeBARDispatcher ----------------------------------------------------------------------------

class LitePCIeBARDispatcher(LiteXModule):
    """
    Routes incoming requests from depacketizer to correct BAR endpoint.
    
    Assumes bar_hit field has been added to the request layout.
    
    Parameters
    ----------
    source : stream.Endpoint
        Request stream from depacketizer (with bar_hit field).
        
    bar_endpoints : dict
        Mapping of BAR number to LitePCIeBAREndpoint instance.
        Example: {0: bar0_ep, 1: bar1_ep, 2: bar2_ep, 5: bar5_ep}
    """
    
    def __init__(self, source, bar_endpoints):
        # Track which BAR is being targeted
        bar_hit = source.bar_hit
        
        # Build case statement for routing
        cases = {}
        
        for bar_num, ep in bar_endpoints.items():
            bar_mask = (1 << bar_num)
            cases[bar_mask] = [
                ep.req_sink.valid.eq(source.valid),
                ep.req_sink.first.eq(source.first),
                ep.req_sink.last.eq(source.last),
                ep.req_sink.we.eq(source.we),
                ep.req_sink.adr.eq(source.adr),
                ep.req_sink.len.eq(source.len),
                ep.req_sink.req_id.eq(source.req_id),
                ep.req_sink.tag.eq(source.tag),
                ep.req_sink.dat.eq(source.dat),
                source.ready.eq(ep.req_sink.ready),
            ]
        
        # Default case: silently drop unhandled BARs
        default_case = [source.ready.eq(1)]
        for bar_num, ep in bar_endpoints.items():
            default_case.append(ep.req_sink.valid.eq(0))
        
        cases["default"] = default_case
        
        # Also need to ensure non-selected endpoints see valid=0
        for bar_num, ep in bar_endpoints.items():
            bar_mask = (1 << bar_num)
            self.comb += If(bar_hit != bar_mask, ep.req_sink.valid.eq(0))
        
        self.comb += Case(bar_hit, cases)


# LitePCIeCompletionArbiter ------------------------------------------------------------------------

class LitePCIeCompletionArbiter(LiteXModule):
    """
    Merges completions from multiple BAR endpoints back to packetizer.
    
    Uses simple priority arbitration (lower BAR number = higher priority).
    Since completions are relatively infrequent, this is sufficient.
    
    Parameters
    ----------
    bar_endpoints : dict
        Mapping of BAR number to LitePCIeBAREndpoint instance.
        
    cpl_sink : stream.Endpoint
        Completion sink going to packetizer.
    """
    
    def __init__(self, bar_endpoints, cpl_sink):
        # Collect completion sources in BAR order for priority
        sources = [(bar_num, ep.cpl_source) for bar_num, ep in sorted(bar_endpoints.items())]
        
        if len(sources) == 0:
            return
        
        if len(sources) == 1:
            # Single BAR - direct connection
            _, src = sources[0]
            self.comb += src.connect(cpl_sink)
            return
        
        # Multiple sources - priority arbitration
        for i, (bar_num, src) in enumerate(sources):
            # Check if any higher-priority source is valid
            if i > 0:
                higher_valid = reduce(or_, [s.valid for _, s in sources[:i]])
            else:
                higher_valid = 0
            
            grant = Signal(name=f"grant_bar{bar_num}")
            self.comb += grant.eq(src.valid & ~higher_valid)
            
            # When granted, connect this source to sink
            self.comb += [
                If(grant,
                    cpl_sink.valid.eq(src.valid),
                    cpl_sink.first.eq(src.first),
                    cpl_sink.last.eq(src.last),
                    cpl_sink.dat.eq(src.dat),
                    cpl_sink.len.eq(src.len),
                    cpl_sink.err.eq(src.err),
                    cpl_sink.tag.eq(src.tag),
                    cpl_sink.adr.eq(src.adr),
                    cpl_sink.req_id.eq(src.req_id),
                    cpl_sink.cmp_id.eq(src.cmp_id),
                    src.ready.eq(cpl_sink.ready),
                ).Else(
                    src.ready.eq(0),
                )
            ]


# LitePCIeMasterArbiter ----------------------------------------------------------------------------

class LitePCIeMasterArbiter(LiteXModule):
    """
    Merges master port requests from multiple BAR endpoints to packetizer.
    
    Handles DMA requests and MSI-X writes. Uses round-robin arbitration
    for fairness between DMA and interrupt traffic.
    
    Parameters
    ----------
    bar_endpoints : dict
        Mapping of BAR number to LitePCIeBAREndpoint instance.
        Only endpoints with active master ports need inclusion.
        
    req_sink : stream.Endpoint
        Request sink going to packetizer.
        
    cpl_source : stream.Endpoint
        Completion source from depacketizer (for DMA read completions).
    """
    
    def __init__(self, bar_endpoints, req_sink, cpl_source):
        sources = [(bar_num, ep.req_source) for bar_num, ep in sorted(bar_endpoints.items())]
        sinks   = [(bar_num, ep.cpl_sink) for bar_num, ep in sorted(bar_endpoints.items())]
        
        if len(sources) == 0:
            return
        
        # Request arbitration
        if len(sources) == 1:
            # Single master - direct connection
            _, src = sources[0]
            self.comb += src.connect(req_sink)
        else:
            # Multiple masters - round-robin with packet boundaries
            n_masters = len(sources)
            current = Signal(max=n_masters)
            in_packet = Signal()
            
            # Build grant logic
            for i, (bar_num, src) in enumerate(sources):
                grant = Signal(name=f"master_grant_bar{bar_num}")
                
                # Grant if: currently selected, or (not in packet and highest priority requesting)
                if i == 0:
                    higher_requesting = 0
                else:
                    higher_requesting = reduce(or_, [s.valid for _, s in sources[:i]])
                
                self.comb += grant.eq(
                    (current == i) |
                    (~in_packet & src.valid & ~higher_requesting)
                )
                
                self.comb += [
                    If(grant,
                        req_sink.valid.eq(src.valid),
                        req_sink.first.eq(src.first),
                        req_sink.last.eq(src.last),
                        req_sink.we.eq(src.we),
                        req_sink.adr.eq(src.adr),
                        req_sink.len.eq(src.len),
                        req_sink.req_id.eq(src.req_id),
                        req_sink.tag.eq(src.tag),
                        req_sink.dat.eq(src.dat),
                        req_sink.channel.eq(src.channel),
                        src.ready.eq(req_sink.ready),
                    ).Else(
                        src.ready.eq(0),
                    )
                ]
            
            # Track packet boundaries for arbitration
            self.sync += [
                If(req_sink.valid & req_sink.ready,
                    If(req_sink.first,
                        in_packet.eq(1),
                    ),
                    If(req_sink.last,
                        in_packet.eq(0),
                        # Advance to next requester (round-robin)
                        If(current == n_masters - 1,
                            current.eq(0),
                        ).Else(
                            current.eq(current + 1),
                        )
                    )
                )
            ]
        
        # Completion routing back to masters (broadcast, they filter by channel)
        if len(sinks) == 1:
            _, snk = sinks[0]
            self.comb += cpl_source.connect(snk)
        else:
            for bar_num, snk in sinks:
                self.comb += [
                    snk.valid.eq(cpl_source.valid),
                    snk.first.eq(cpl_source.first),
                    snk.last.eq(cpl_source.last),
                    snk.dat.eq(cpl_source.dat),
                    snk.len.eq(cpl_source.len),
                    snk.end.eq(cpl_source.end),
                    snk.err.eq(cpl_source.err),
                    snk.tag.eq(cpl_source.tag),
                    snk.adr.eq(cpl_source.adr),
                    snk.req_id.eq(cpl_source.req_id),
                    snk.cmp_id.eq(cpl_source.cmp_id),
                ]
            
            # Ready when any sink can accept
            self.comb += cpl_source.ready.eq(reduce(or_, [snk.ready for _, snk in sinks]))