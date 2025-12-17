LitePCIe Architecture Analysis
==============================

This document analyzes the LitePCIe architecture to understand how it handles
TLP routing and what modifications are needed for multi-BAR support.

Current Architecture
--------------------

LitePCIe uses a single BAR (BAR0) with the following data flow:

.. code-block:: text

                                HOST
                                  │
                                  │ PCIe Link
                                  │
                        ┌─────────▼─────────┐
                        │       PHY         │
                        │   (single BAR0)   │
                        └─────────┬─────────┘
                                  │
                        ┌─────────▼─────────┐
                        │   Depacketizer    │
                        │   (extracts TLP   │
                        │    headers)       │
                        └─────────┬─────────┘
                                  │
                  ┌───────────────▼───────────────┐
                  │           CROSSBAR            │
                  │                               │
                  │  Slave Side    Master Side    │
                  │  (Host→FPGA)   (FPGA→Host)    │
                  │                               │
                  │  ┌─────────┐  ┌──────────┐    │
                  │  │phy_slave│  │phy_master│    │
                  │  └────┬────┘  └────┬─────┘    │
                  │       │            │          │
                  │  ┌────▼────┐  ┌────▼────┐     │
                  │  │ Slave   │  │ Master  │     │
                  │  │ Ports   │  │ Ports   │     │
                  │  │ (0..N)  │  │ (0..N)  │     │
                  └──┴────┬────┴──┴────┬────┴─────┘
                          │            │
             ┌────────────┼────────────┼────────────┐
             │            │            │            │
             ▼            ▼            ▼            ▼
        ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐
        │Wishbone │  │ Custom  │  │  DMA    │  │  DMA    │
        │ Bridge  │  │ Slave   │  │ Writer  │  │ Reader  │
        │(to CSRs)│  │         │  │(to host)│  │(fr host)│
        └─────────┘  └─────────┘  └─────────┘  └─────────┘
          SLAVE        SLAVE        MASTER       MASTER
        (Host writes) (Host writes) (FPGA writes) (FPGA reads)

**Slave Ports** - Host initiates (Memory Read/Write to BAR0):

- Multiple handlers can register for different address ranges *within* BAR0
- Crossbar routes by address decoder function

**Master Ports** - FPGA initiates (DMA to host memory):

- Multiple DMA engines can send TLPs to host
- Crossbar arbitrates access to the single TX path

Crossbar Address Decoding
-------------------------

The crossbar uses user-provided ``address_decoder`` functions to route
incoming requests to the appropriate slave port.

When requesting a slave port:

.. code-block:: python

    # Get a slave port with custom address decoder
    port = endpoint.crossbar.get_slave_port(
        address_decoder = lambda a: (a >= 0x0000) & (a < 0x1000)
    )

The crossbar dispatcher uses these decoders:

.. code-block:: python

    # crossbar.py:64-65
    for i, s in enumerate(slaves):
        self.comb += s_dispatcher.sel[i].eq(
            s.address_decoder(slave.source.adr)
        )

Visual representation:

.. code-block:: text

                        slave.source.adr = 0x0500
                                  │
                                  ▼
                ┌─────────────────────────────────┐
                │           Dispatcher            │
                │                                 │
                │  sel[0] = slave0.decoder(0x500) │ → 1 (matches)
                │  sel[1] = slave1.decoder(0x500) │ → 0 (no match)
                │  sel[2] = slave2.decoder(0x500) │ → 0 (no match)
                │                                 │
                └─────────────┬───────────────────┘
                              │
                              ▼
                         slave0.source

Current Usage in Practice
~~~~~~~~~~~~~~~~~~~~~~~~~

Despite the infrastructure, **nobody actually uses address-based routing**:

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Location
     - Usage
   * - ``litepcie/frontend/wishbone.py``
     - ``lambda a: 1`` (accept all)
   * - ``litepcie/frontend/ptm/core.py``
     - ``lambda a: 0`` (placeholder)
   * - ``litepcie/gen.py``
     - No decoder passed (uses default)
   * - ``litex_wr_nic/``
     - No ``get_slave_port`` calls

Everyone uses a single slave port with "accept all" because:

1. Single BAR0 configuration
2. Single Wishbone bridge to SoC CSR bus
3. Address decoding happens downstream in Wishbone interconnect

Depacketizer Address Mask
-------------------------

The ``address_mask`` parameter in the depacketizer is often misunderstood.
It does **NOT** perform routing - it simply masks off upper address bits
to get the offset within the BAR:

.. code-block:: python

    # depacketizer.py:400
    req_source.adr.eq(tlp_req.address & (~address_mask))

Since the PCIe hard IP already matches the BAR range and provides the offset,
this mask is primarily for compatibility/safety.

Multi-BAR Architecture
----------------------

For BSA compliance with multiple BARs, the architecture needs modification.
The key insight is that the packetizer/depacketizer don't need to be
duplicated - TLP format is identical regardless of which BAR is targeted.

Recommended Architecture
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

                 RX Path                              TX Path
                 -------                              -------

                    PHY                                  PHY
           (source + bar_hit)                          (sink)
                    │                                    ▲
                    ▼                                    │
         ┌──────────────────────┐          ┌──────────────────────┐
         │  Single Depacketizer │          │  Single Packetizer   │
         │  (preserves bar_hit) │          │                      │
         └──────────┬───────────┘          └──────────▲───────────┘
                    │                                 │
                    ▼                                 │
         ┌──────────────────────┐          ┌──────────────────────┐
         │     BAR Router       │          │      Arbiter         │
         │   (demux by bar_hit) │          │  (merge completions) │
         └──────────┬───────────┘          └──────────▲───────────┘
                    │                                 │
            ┌───────┼───────┬───────┐         ┌───────┴───────┬───────┐
            │       │       │       │         │       │       │       │
            ▼       ▼       ▼       ▼         │       │       │       │
         ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐   ┌──┴──┐ ┌──┴──┐ ┌──┴──┐ ┌──┴──┐
         │BAR0 │ │BAR1 │ │BAR2 │ │BAR5 │   │BAR0 │ │BAR1 │ │BAR2 │ │BAR5 │
         │CSRs │ │BRAM │ │MSI-X│ │PBA  │   │ cmp │ │ cmp │ │ cmp │ │ cmp │
         └─────┘ └─────┘ └─────┘ └─────┘   └─────┘ └─────┘ └─────┘ └─────┘
            │       │       │       │         ▲       ▲       ▲       ▲
            └───────┴───────┴───────┴─────────┴───────┴───────┴───────┘
                             (read completions flow back)

Key design decisions:

1. **Single depacketizer** - TLP extraction is identical regardless of BAR
2. **Single packetizer** - TLP building is identical regardless of BAR
3. **BAR router after depacketizer** - Demux requests by ``bar_hit``
4. **Arbiter before packetizer** - Merge completions from all BARs

Why bar_hit Eliminates the Crossbar
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

With ``bar_hit`` available, the crossbar's address_decoder mechanism becomes
redundant for the slave path:

.. list-table:: Routing Comparison
   :header-rows: 1
   :widths: 25 35 40

   * - Aspect
     - Without bar_hit
     - With bar_hit
   * - Routing method
     - Address decoder lambdas
     - Simple one-hot mux
   * - Slave path
     - Crossbar dispatcher
     - Direct bar_hit demux
   * - Multiple slaves per BAR
     - Supported
     - Not needed for BSA
   * - Complexity
     - High
     - Low

**Master Path (FPGA → Host):** ``bar_hit`` doesn't apply here - we're targeting
host memory addresses, not our BARs:

.. list-table:: Master Path Arbitration
   :header-rows: 1
   :widths: 40 60

   * - Use Case
     - Need Arbitration?
   * - Multiple DMA engines
     - Yes - arbitrate TX path
   * - Single DMA engine (BSA)
     - No - direct connection

BSA Exerciser Architecture (No Crossbar)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For the BSA exerciser with a single DMA engine and dedicated per-BAR handlers,
the routing becomes trivial:

.. code-block:: text

                                  HOST
                                    │
                          ┌─────────▼─────────┐
                          │       PHY         │
                          │    bar_hit[6:0]   │
                          └────┬─────────┬────┘
                          RX   │         │   TX
                               │         │
                        ┌──────▼──┐   ┌──▲──────┐
                        │ Depack. │   │ Packet. │
                        └────┬────┘   └────▲────┘
                             │             │
            ┌────────────────┼─────────────┤
            │                │             │
            │         ┌──────▼──────┐      │
            │         │  bar_hit    │      │
            │         │   demux     │      │
            │         └──┬──┬──┬──┬─┘      │
            │            │  │  │  │        │
            │     ┌──────┘  │  │  └──────┐ │
            │     │    ┌────┘  └────┐    │ │
            │     ▼    ▼            ▼    ▼ │
            │  ┌─────┬─────┐     ┌─────┬─────┐
            │  │BAR0 │BAR1 │     │BAR2 │BAR5 │
            │  │CSRs │BRAM │     │MSI-X│ PBA │
            │  └──┬──┴──┬──┘     └──┬──┴──┬──┘
            │     │     │           │     │
            │     └──┬──┘           └──┬──┘
            │        │                 │
            │        └────────┬────────┘
            │                 │
            │          ┌──────▼──────┐
            │          │   Arbiter   │
            │          │(completions)│
            │          └──────┬──────┘
            │                 │
            │                 ├─────────────────────┘
            │                 │             (to Packetizer)
            │                 │
            │  ┌──────────────┘
            │  │
            │  │      ┌───────────────┐
            └──┼─────►│  DMA Engine   │
               │      │ (master only) │
               │      └───────┬───────┘
               │              │
               └──────────────┘
                  (DMA reqs go direct to Packetizer)
                  (DMA completions come from Depacketizer)

**The crossbar disappears entirely.** What remains:

1. **bar_hit demux** - Routes host requests to correct BAR handler (one-hot mux)
2. **Completion arbiter** - Merges read completions from 4 BAR handlers
3. **Direct DMA path** - Single master, no arbitration needed

Data Flow Summary
~~~~~~~~~~~~~~~~~

**Host Read from BAR1 (example):**

.. code-block:: text

    1. Host issues MRd to BAR1 address
    2. PHY receives TLP, sets bar_hit[1]=1
    3. Depacketizer extracts header, preserves bar_hit
    4. bar_hit demux routes to BAR1 handler (BRAM)
    5. BRAM reads data, generates completion
    6. Completion arbiter forwards to Packetizer
    7. Packetizer builds CplD TLP
    8. PHY transmits to host

**DMA Write to Host Memory:**

.. code-block:: text

    1. Software writes BAR0 CSRs to configure DMA
    2. Software triggers DMA via BAR0
    3. DMA engine generates MWr TLP with host address
    4. Direct path to Packetizer (no crossbar)
    5. PHY transmits to host
    6. (No completion for posted writes)

Implementation Options
----------------------

Option 1: Extend Crossbar
~~~~~~~~~~~~~~~~~~~~~~~~~

Modify the address decoder to also consider ``bar_hit``:

.. code-block:: python

    # Extended address_decoder signature
    address_decoder = lambda a, bar: (bar == 0) & (a < 0x1000)

    # Modified crossbar dispatch
    self.comb += s_dispatcher.sel[i].eq(
        s.address_decoder(slave.source.adr, slave.source.bar_hit)
    )

Pros:
  - Minimal changes to existing architecture
  - Reuses existing crossbar arbitration

Cons:
  - Adds complexity to all address decoders
  - Crossbar overhead for simple per-BAR routing

Option 2: Dedicated BAR Router
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Add a simple BAR routing layer, bypass crossbar for slave path:

.. code-block:: python

    class BARRouter(Module):
        def __init__(self, data_width):
            self.sink = stream.Endpoint(request_layout(data_width))

            # One source per BAR
            self.bar0_source = stream.Endpoint(request_layout(data_width))
            self.bar1_source = stream.Endpoint(request_layout(data_width))
            self.bar2_source = stream.Endpoint(request_layout(data_width))
            self.bar5_source = stream.Endpoint(request_layout(data_width))

            # Simple one-hot routing by bar_hit
            self.comb += [
                If(self.sink.bar_hit[0],
                    self.sink.connect(self.bar0_source)
                ).Elif(self.sink.bar_hit[1],
                    self.sink.connect(self.bar1_source)
                ).Elif(self.sink.bar_hit[2],
                    self.sink.connect(self.bar2_source)
                ).Elif(self.sink.bar_hit[5],
                    self.sink.connect(self.bar5_source)
                )
            ]

Pros:
  - Clean separation of concerns
  - No crossbar overhead for simple routing
  - Easier to understand and debug

Cons:
  - New module to maintain
  - Need separate arbiter for completions

Recommendation
~~~~~~~~~~~~~~

For the BSA exerciser, **Option 2 (dedicated BAR router)** is recommended:

1. Each BAR has exactly one handler - no need for address-based sub-routing
2. Simpler data flow for verification and debugging
3. Crossbar can still be used for master path (DMA engine arbitration)
4. Cleaner architecture matches the BSA spec's BAR assignments
