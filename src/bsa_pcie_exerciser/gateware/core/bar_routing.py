#
# LitePCIe Multi-BAR Routing Infrastructure
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Provides BAR-based request routing and completion arbitration for
# multi-BAR PCIe endpoint designs.
#

from migen import *
from functools import reduce
from operator import or_

from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import *


# =============================================================================
# BAR Dispatcher
# =============================================================================

class LitePCIeBARDispatcher(LiteXModule):
    """
    Routes incoming TLP requests to the appropriate BAR handler based on bar_hit.

    The bar_hit field is a one-hot encoded signal from the PCIe IP indicating
    which BAR was targeted by the incoming request:
        bar_hit[0] = BAR0 hit
        bar_hit[1] = BAR1 hit
        ...
        bar_hit[5] = BAR5 hit

    Parameters
    ----------
    source : stream.Endpoint
        Request stream from depacketizer (request_layout with bar_hit field).

    bar_sinks : dict
        Mapping of BAR number to sink endpoint.
        Example: {0: bar0_sink, 1: bar1_sink, 2: bar2_sink, 5: bar5_sink}

    default_bar : int, optional
        BAR to route to if no bar_hit bits are set (default: 0).
        Set to None to drop unmatched requests.
    """

    def __init__(self, source, bar_sinks, default_bar=0):
        self.source    = source
        self.bar_sinks = bar_sinks

        # # #

        bar_hit = source.bar_hit

        # Track which BAR is selected (for multi-cycle packets)
        bar_selected = Signal(6, reset_less=True)
        in_packet    = Signal()

        # Latch BAR selection on first beat of packet
        self.sync += [
            If(source.valid & source.ready,
                If(source.first,
                    bar_selected.eq(bar_hit),
                    in_packet.eq(1),
                ),
                If(source.last,
                    in_packet.eq(0),
                ),
            ),
        ]

        # Use latched value during packet, live value on first beat
        bar_active = Signal(6)
        self.comb += [
            If(in_packet,
                bar_active.eq(bar_selected),
            ).Else(
                bar_active.eq(bar_hit),
            ),
        ]

        # Build routing logic
        # Default: no sink selected, drop the request
        source_ready = Signal()
        self.comb += source.ready.eq(source_ready)

        # For each BAR, check if it's targeted and connect
        cases = {}

        for bar_num, sink in bar_sinks.items():
            bar_mask = (1 << bar_num)

            # Create case for this BAR
            case_stmts = [
                sink.valid.eq(source.valid),
                sink.first.eq(source.first),
                sink.last.eq(source.last),
                sink.we.eq(source.we),
                sink.adr.eq(source.adr),
                sink.len.eq(source.len),
                sink.req_id.eq(source.req_id),
                sink.tag.eq(source.tag),
                sink.dat.eq(source.dat),
                sink.channel.eq(source.channel),
                sink.user_id.eq(source.user_id),
                sink.bar_hit.eq(source.bar_hit),
                sink.first_be.eq(source.first_be),
                sink.last_be.eq(source.last_be),
                source_ready.eq(sink.ready),
            ]

            # Include attr/at if present in layout
            if hasattr(source, 'attr'):
                case_stmts.append(sink.attr.eq(source.attr))
            if hasattr(source, 'at'):
                case_stmts.append(sink.at.eq(source.at))

            cases[bar_mask] = case_stmts

        # Default case
        if default_bar is not None and default_bar in bar_sinks:
            # Route to default BAR
            cases["default"] = cases[1 << default_bar]
        else:
            # Drop unmatched requests (accept but don't forward)
            default_stmts = [source_ready.eq(1)]
            for sink in bar_sinks.values():
                default_stmts.append(sink.valid.eq(0))
            cases["default"] = default_stmts

        # Ensure non-selected sinks see valid=0
        for bar_num, sink in bar_sinks.items():
            bar_mask = (1 << bar_num)
            self.comb += If(bar_active != bar_mask, sink.valid.eq(0))

        self.comb += Case(bar_active, cases)


# =============================================================================
# Completion Arbiter
# =============================================================================

class LitePCIeCompletionArbiter(LiteXModule):
    """
    Merges completion streams from multiple BAR handlers back to the packetizer.

    Uses priority arbitration with lower BAR numbers having higher priority.
    Respects packet boundaries (won't switch mid-packet).

    Parameters
    ----------
    bar_sources : dict
        Mapping of BAR number to completion source endpoint.
        Example: {0: bar0_cpl_source, 1: bar1_cpl_source}

    sink : stream.Endpoint
        Completion sink going to packetizer.
    """

    def __init__(self, bar_sources, sink):
        self.bar_sources = bar_sources
        self.sink        = sink

        # # #

        if len(bar_sources) == 0:
            # No sources - tie off sink
            self.comb += sink.valid.eq(0)
            return

        if len(bar_sources) == 1:
            # Single source - direct connection
            _, source = list(bar_sources.items())[0]
            self.comb += source.connect(sink)
            return

        # Multiple sources - priority arbitration with packet tracking
        sources = [(bar_num, src) for bar_num, src in sorted(bar_sources.items())]

        # Track current grant and whether we're mid-packet
        current_grant = Signal(max=len(sources), reset_less=True)
        in_packet     = Signal()

        # Determine which source is requesting (priority encoded)
        requesting = Signal(len(sources))
        for i, (_, src) in enumerate(sources):
            self.comb += requesting[i].eq(src.valid)

        # Priority encoder - find highest priority (lowest BAR) requesting
        next_grant = Signal(max=len(sources))
        for i in range(len(sources)):
            higher_requesting = reduce(or_, [requesting[j] for j in range(i)], 0) if i > 0 else 0
            self.comb += If(requesting[i] & ~higher_requesting,
                next_grant.eq(i),
            )

        # Grant logic: stick with current during packet, otherwise take next
        grant = Signal(max=len(sources))
        self.comb += [
            If(in_packet,
                grant.eq(current_grant),
            ).Else(
                grant.eq(next_grant),
            ),
        ]

        # Track packet boundaries
        self.sync += [
            If(sink.valid & sink.ready,
                If(sink.first,
                    current_grant.eq(grant),
                    in_packet.eq(1),
                ),
                If(sink.last,
                    in_packet.eq(0),
                ),
            ),
        ]

        # Mux selected source to sink
        # completion_layout fields: req_id, cmp_id, adr, len, end, err, tag, status, byte_count, dat, channel, user_id
        cases = {}
        for i, (bar_num, src) in enumerate(sources):
            cases[i] = [
                sink.valid.eq(src.valid),
                sink.first.eq(src.first),
                sink.last.eq(src.last),
                sink.dat.eq(src.dat),
                sink.len.eq(src.len),
                sink.end.eq(src.end),
                sink.err.eq(src.err),
                sink.tag.eq(src.tag),
                sink.status.eq(src.status),
                sink.byte_count.eq(src.byte_count),
                sink.adr.eq(src.adr),
                sink.req_id.eq(src.req_id),
                sink.cmp_id.eq(src.cmp_id),
                sink.channel.eq(src.channel),
                sink.user_id.eq(src.user_id),
                src.ready.eq(sink.ready),
            ]

        # Default: no grant
        default_stmts = [sink.valid.eq(0)]
        for _, src in sources:
            default_stmts.append(src.ready.eq(0))
        cases["default"] = default_stmts

        self.comb += Case(grant, cases)

        # Non-granted sources should not see ready
        for i, (_, src) in enumerate(sources):
            self.comb += If(grant != i, src.ready.eq(0))


# =============================================================================
# Master Arbiter (for DMA/MSI-X outgoing requests)
# =============================================================================

class LitePCIeMasterArbiter(LiteXModule):
    """
    Merges master request streams from multiple BAR handlers to the packetizer.

    Used for DMA read/write requests and MSI-X memory writes.
    Uses round-robin arbitration for fairness.

    Parameters
    ----------
    bar_sources : dict
        Mapping of BAR number to master request source endpoint.

    sink : stream.Endpoint
        Request sink going to packetizer.

    Also handles completion routing back to the correct BAR based on channel.
    """

    def __init__(self, bar_sources, sink):
        self.bar_sources = bar_sources
        self.sink        = sink

        # # #

        if len(bar_sources) == 0:
            self.comb += sink.valid.eq(0)
            return

        if len(bar_sources) == 1:
            _, source = list(bar_sources.items())[0]
            self.comb += source.connect(sink)
            return

        # Multiple sources - round-robin arbitration
        sources = [(bar_num, src) for bar_num, src in sorted(bar_sources.items())]
        n_sources = len(sources)

        # Round-robin state
        rr_state  = Signal(max=n_sources)
        in_packet = Signal()
        current_grant = Signal(max=n_sources, reset_less=True)

        # Find next valid source starting from rr_state
        next_grant = Signal(max=n_sources)
        next_valid = Signal()

        # Priority scan from current rr_state
        for offset in range(n_sources):
            check_idx = Signal(max=n_sources, name=f"check_{offset}")
            self.comb += check_idx.eq((rr_state + offset) % n_sources)

            # Check if this source is valid
            for i, (_, src) in enumerate(sources):
                self.comb += If((check_idx == i) & src.valid & ~next_valid,
                    next_grant.eq(i),
                    next_valid.eq(1),
                )

        # Grant logic
        grant = Signal(max=n_sources)
        self.comb += [
            If(in_packet,
                grant.eq(current_grant),
            ).Else(
                grant.eq(next_grant),
            ),
        ]

        # Update round-robin state and packet tracking
        self.sync += [
            If(sink.valid & sink.ready,
                If(sink.first,
                    current_grant.eq(grant),
                    in_packet.eq(1),
                ),
                If(sink.last,
                    in_packet.eq(0),
                    # Advance round-robin
                    If(grant == n_sources - 1,
                        rr_state.eq(0),
                    ).Else(
                        rr_state.eq(grant + 1),
                    ),
                ),
            ),
        ]

        # Mux selected source to sink
        cases = {}
        for i, (bar_num, src) in enumerate(sources):
            case_stmts = [
                sink.valid.eq(src.valid),
                sink.first.eq(src.first),
                sink.last.eq(src.last),
                sink.we.eq(src.we),
                sink.adr.eq(src.adr),
                sink.len.eq(src.len),
                sink.req_id.eq(src.req_id),
                sink.tag.eq(src.tag),
                sink.dat.eq(src.dat),
                sink.channel.eq(src.channel),
                sink.user_id.eq(src.user_id),
                src.ready.eq(sink.ready),
            ]

            if hasattr(src, 'attr'):
                case_stmts.append(sink.attr.eq(src.attr))
            if hasattr(src, 'at'):
                case_stmts.append(sink.at.eq(src.at))

            cases[i] = case_stmts

        default_stmts = [sink.valid.eq(0)]
        for _, src in sources:
            default_stmts.append(src.ready.eq(0))
        cases["default"] = default_stmts

        self.comb += Case(grant, cases)

        for i, (_, src) in enumerate(sources):
            self.comb += If(grant != i, src.ready.eq(0))


# =============================================================================
# Stub BAR Handler (for unimplemented BARs)
# =============================================================================

class LitePCIeStubBARHandler(LiteXModule):
    """
    Stub handler for BARs that aren't yet implemented.

    Accepts incoming requests and either:
    - Silently drops them (for writes)
    - Returns Unsupported Request completion (for reads)

    Parameters
    ----------
    data_width : int
        Data width in bits.

    bar_num : int
        BAR number (for debug).

    return_ur : bool
        If True, return UR completion for reads. If False, silently drop.
    """

    def __init__(self, data_width, bar_num=0, return_ur=False):
        # Request sink (from dispatcher)
        self.req_sink = stream.Endpoint(request_layout(data_width))

        # Completion source (to arbiter)
        self.cpl_source = stream.Endpoint(completion_layout(data_width))

        # Master interfaces (unused for stub, but needed for arbiter)
        self.req_source = stream.Endpoint(request_layout(data_width))
        self.cpl_sink   = stream.Endpoint(completion_layout(data_width))

        # # #

        if not return_ur:
            # Simple: just accept and drop everything
            self.comb += [
                self.req_sink.ready.eq(1),
                self.cpl_source.valid.eq(0),
                self.req_source.valid.eq(0),
                self.cpl_sink.ready.eq(1),
            ]
        else:
            # Return UR completion for read requests
            # Latch request info
            req_valid  = Signal()
            req_tag    = Signal(8)
            req_req_id = Signal(16)
            req_is_read = Signal()

            self.sync += [
                If(self.req_sink.valid & self.req_sink.ready & self.req_sink.first,
                    req_valid.eq(~self.req_sink.we),  # Only for reads
                    req_tag.eq(self.req_sink.tag),
                    req_req_id.eq(self.req_sink.req_id),
                ),
                If(self.cpl_source.valid & self.cpl_source.ready,
                    req_valid.eq(0),
                ),
            ]

            self.comb += [
                # Accept requests
                self.req_sink.ready.eq(~req_valid | (self.req_sink.we)),

                # Generate UR completion
                # completion_layout: req_id, cmp_id, adr, len, end, err, tag, dat, channel, user_id
                self.cpl_source.valid.eq(req_valid),
                self.cpl_source.first.eq(1),
                self.cpl_source.last.eq(1),
                self.cpl_source.dat.eq(0),
                self.cpl_source.len.eq(0),
                self.cpl_source.end.eq(1),
                self.cpl_source.err.eq(1),  # UR
                self.cpl_source.tag.eq(req_tag),
                self.cpl_source.req_id.eq(req_req_id),
                self.cpl_source.cmp_id.eq(0),
                self.cpl_source.adr.eq(0),
                self.cpl_source.channel.eq(0),
                self.cpl_source.user_id.eq(0),

                # Unused master interfaces
                self.req_source.valid.eq(0),
                self.cpl_sink.ready.eq(1),
            ]
