#
# LitePCIe Multi-BAR Endpoint
#
# Copyright (c) 2025-2026 Shareef Jalloq
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

    Note
    ----
    The PHY reference is stored as ``_phy`` and excluded from CSR collection
    via ``autocsr_exclude`` to prevent the PHY's CSRs being duplicated under
    this module's namespace.

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

    tx_filter : LiteXModule, optional
        Optional TX path filter module (e.g., PASID prefix injector).
        Must have sink (input) and source (output) stream endpoints with phy_layout.
        If provided, inserted between packetizer and PHY: packetizer → tx_filter → PHY.

    with_ats_inv : bool
        Enable ATS Invalidation Message handling. When True, the depacketizer
        will parse ATS Invalidation Messages and expose ats_inv_source.

    raw_tx_sources : list, optional
        List of additional raw TX sources (phy_layout endpoints) to be arbitrated
        with the main TX path. These bypass the packetizer and go directly to the
        PHY after the tx_filter (if any). Useful for Message TLPs like ATS
        Invalidation Completions that don't need PASID prefix injection.
    """

    autocsr_exclude = {"phy"}  # Exclude the property from CSR collection

    def __init__(self, phy,
                 endianness="big",
                 address_width=32,
                 max_pending_requests=4,
                 bar_enables=None,
                 bar_handlers=None,
                 tx_filter=None,
                 with_ats_inv=False,
                 with_configuration=False,
                 raw_tx_sources=None):

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

        # Build capabilities list
        depack_capabilities = ["REQUEST", "COMPLETION"]
        if with_ats_inv:
            depack_capabilities.append("ATS_INV")
        if with_configuration:
            depack_capabilities.append("CONFIGURATION")

        self.depacketizer = LitePCIeTLPDepacketizer(
            data_width   = phy.data_width,
            endianness   = endianness,
            address_mask = phy.bar0_mask,
            capabilities = depack_capabilities,
        )

        # Expose ATS Invalidation source if enabled
        if with_ats_inv:
            self.ats_inv_source = self.depacketizer.ats_inv_source
        if with_configuration:
            self.conf_source = self.depacketizer.conf_source

        self.packetizer = LitePCIeTLPPacketizer(
            data_width    = phy.data_width,
            endianness    = endianness,
            address_width = address_width,
            capabilities  = ["REQUEST", "COMPLETION"],
        )

        # Connect PHY
        # RX path: PHY → depacketizer
        self.comb += phy.source.connect(self.depacketizer.sink)

        # TX path: packetizer → [tx_filter] → [arbiter] → PHY
        # Determine the main TX source (after optional filtering)
        if tx_filter is not None:
            self.tx_filter = tx_filter
            self.comb += self.packetizer.source.connect(tx_filter.sink)
            main_tx_source = tx_filter.source
        else:
            main_tx_source = self.packetizer.source

        # If raw TX sources exist (e.g., Message TLPs), arbitrate them with main TX
        if raw_tx_sources:
            # Simple 2-source priority arbiter: raw sources have priority (they're rare)
            # Main TX path has lower priority, raw sources (like Message TLPs) take over
            # when they have data. Respects packet boundaries.

            raw_src = raw_tx_sources[0]  # Currently only support one raw source

            # Track if we're mid-packet on the main TX path
            main_in_packet = Signal()
            self.sync += If(main_tx_source.valid & phy.sink.ready,
                If(main_tx_source.first,
                    main_in_packet.eq(1),
                ),
                If(main_tx_source.last,
                    main_in_packet.eq(0),
                ),
            )

            # Grant raw source when it has data AND we're not mid-main-packet
            grant_raw = Signal()
            self.comb += grant_raw.eq(raw_src.valid & ~main_in_packet)

            # Mux the outputs
            self.comb += [
                phy.sink.valid.eq(Mux(grant_raw, raw_src.valid, main_tx_source.valid)),
                phy.sink.first.eq(Mux(grant_raw, raw_src.first, main_tx_source.first)),
                phy.sink.last.eq(Mux(grant_raw, raw_src.last, main_tx_source.last)),
                phy.sink.dat.eq(Mux(grant_raw, raw_src.dat, main_tx_source.dat)),
                phy.sink.be.eq(Mux(grant_raw, raw_src.be, main_tx_source.be)),
                # Ready back to sources
                main_tx_source.ready.eq(~grant_raw & phy.sink.ready),
                raw_src.ready.eq(grant_raw & phy.sink.ready),
            ]
        else:
            # No additional sources, direct connection
            self.comb += main_tx_source.connect(phy.sink)

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
        # Note: .connect() cannot be used multiple times, so use explicit signals.

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

    Useful for attaching standard LitePCIe frontend components
    (like LitePCIeWishboneBridge) to a specific BAR's crossbar.

    Parameters
    ----------
    crossbar : LitePCIeCrossbar
        The crossbar for this BAR.

    phy : S7PCIEPHY
        PHY instance (for phy.id used in completions).
    """

    autocsr_exclude = {"phy"}  # Exclude the property from CSR collection

    def __init__(self, crossbar, phy):
        self.crossbar   = crossbar
        self._phy       = phy

        self.data_width = phy.data_width

    @property
    def phy(self):
        """Expose PHY for external access."""
        return self._phy
