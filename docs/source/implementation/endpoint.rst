Multi-BAR Endpoint
==================

The ``LitePCIeMultiBAREndpoint`` extends standard LitePCIe functionality to
support multiple BARs with independent routing based on the ``bar_hit`` field.

Source: ``src/bsa_pcie_exerciser/gateware/core/multibar_endpoint.py``

Overview
--------

Standard LitePCIe assumes a single BAR. The multi-BAR endpoint adds:

* Per-BAR request routing via ``bar_hit`` field
* Custom BAR handlers (not just crossbars)
* Completion arbitration from multiple BARs
* Optional TX filtering (for PASID prefix injection)
* Raw TX source arbitration (for Message TLPs)

Architecture
------------

::

    PHY Source                                              PHY Sink
        │                                                      ▲
        ▼                                                      │
    ┌───────────────┐                              ┌───────────┴───────────┐
    │ Depacketizer  │                              │   TX Arbiter          │
    │               │                              │  (main + raw sources) │
    │ req_source ───┼──┐                           └───────────▲───────────┘
    │ cmp_source ───┼──┼──┐                                    │
    │ ats_inv_src ──┼──┼──┼──► (to ATS Inv Handler)            │
    └───────────────┘  │  │                        ┌───────────┴───────────┐
                       │  │                        │   PASID Injector      │
                       │  │                        │   (tx_filter)         │
                       │  │                        └───────────▲───────────┘
                       │  │                                    │
                       │  │                        ┌───────────┴───────────┐
                       │  └───────────────────────►│    Packetizer         │
                       │    (completion routing)   │                       │
                       │                           │ cmp_sink ◄────────────┼─┐
                       ▼                           │ req_sink ◄────────────┼─┼─┐
    ┌─────────────────────────────────┐            └───────────────────────┘ │ │
    │       BAR Dispatcher            │                                      │ │
    │                                 │            ┌─────────────────────────┼─┘
    │ Routes by bar_hit[5:0]          │            │  Completion Arbiter    │
    └─────────┬───┬───┬───┬───┬───┬───┘            │  (round-robin)         │
              │   │   │   │   │   │                └──────────▲─────────────┘
              ▼   ▼   ▼   ▼   ▼   ▼                           │
           BAR0 BAR1 BAR2 BAR3 BAR4 BAR5          ┌───────────┴───────────┐
              │   │   │   │   │   │               │   Master Arbiter      │
              └───┴───┴───┴───┴───┴───────────────►  (for DMA/MSI-X)      │
                  (completions to arbiter)        └───────────────────────┘

BAR Dispatcher
--------------

The ``LitePCIeBARDispatcher`` routes incoming requests based on ``bar_hit``:

.. code-block:: python

    class LitePCIeBARDispatcher(LiteXModule):
        def __init__(self, source, bar_sinks, default_bar=0):
            # Route based on bar_hit field
            for bar_num, sink in bar_sinks.items():
                self.comb += If(source.bar_hit[bar_num],
                    source.connect(sink, ...),
                )

Only one ``bar_hit`` bit should be set at a time. If none match, the request
goes to ``default_bar``.

BAR Handlers
------------

Each BAR can have either:

1. **Crossbar**: Full LitePCIe crossbar with slave/master ports
2. **Custom Handler**: Module with ``req_sink`` and ``cpl_source`` endpoints
3. **Stub Handler**: Returns UR (Unsupported Request) for disabled BARs

Custom handlers are used for:

* BAR1: DMA buffer with optimized memory access
* BAR2: MSI-X table with dual-port memory
* BAR5: MSI-X PBA with register array

TX Path Components
------------------

PASID Prefix Injector (tx_filter)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When ``tx_filter`` is provided, it's inserted between packetizer and PHY:

.. code-block:: python

    if tx_filter is not None:
        self.comb += self.packetizer.source.connect(tx_filter.sink)
        main_tx_source = tx_filter.source
    else:
        main_tx_source = self.packetizer.source

See :doc:`pasid` for details.

Raw TX Source Arbitration
~~~~~~~~~~~~~~~~~~~~~~~~~

Some TLPs bypass the packetizer (e.g., ATS Invalidation Completion messages).
These are arbitrated with the main TX path:

.. code-block:: python

    # Grant raw source when it has data AND main path not mid-packet
    grant_raw = Signal()
    self.comb += grant_raw.eq(raw_src.valid & ~main_in_packet)

    # Mux to PHY
    self.comb += [
        phy.sink.valid.eq(Mux(grant_raw, raw_src.valid, main_tx_source.valid)),
        # ... other signals ...
    ]

This ensures packet boundaries are respected—a raw TLP won't interrupt
a multi-beat Memory Write.

Configuration
-------------

The endpoint is configured via constructor parameters:

.. code-block:: python

    self.pcie_endpoint = LitePCIeMultiBAREndpoint(self.pcie_phy,
        endianness           = "big",
        max_pending_requests = 4,
        bar_enables  = {0: True, 1: True, 2: True, 3: False, 4: False, 5: True},
        bar_handlers = {
            1: self.dma_handler,
            2: self.msix_table,
            5: self.msix_pba,
        },
        tx_filter    = self.pasid_injector,
        with_ats_inv = True,
        raw_tx_sources = [self.ats_invalidation.msg_source],
    )
