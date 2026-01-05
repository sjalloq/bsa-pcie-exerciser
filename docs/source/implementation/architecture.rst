Top-Level Architecture
======================

The BSA Exerciser is implemented as a LiteX SoC (``BSAExerciserSoC``) that
integrates a PCIe endpoint with DMA, interrupt, and ATS subsystems.

SoC Block Diagram
-----------------

::

    ┌─────────────────────────────────────────────────────────────────────┐
    │                        BSAExerciserSoC                              │
    │                                                                     │
    │  ┌─────────┐    ┌──────────────────────────────────────────────┐   │
    │  │  S7PCI  │    │          LitePCIeMultiBAREndpoint            │   │
    │  │   PHY   │◄──►│                                              │   │
    │  └─────────┘    │  ┌─────────────┐  ┌─────────────────────┐   │   │
    │                 │  │Depacketizer │  │    Packetizer       │   │   │
    │                 │  └──────┬──────┘  └──────────▲──────────┘   │   │
    │                 │         │                    │              │   │
    │                 │         ▼                    │              │   │
    │                 │  ┌─────────────┐      ┌──────┴──────┐       │   │
    │                 │  │    BAR      │      │   Master    │       │   │
    │                 │  │ Dispatcher  │      │   Arbiter   │       │   │
    │                 │  └──────┬──────┘      └──────▲──────┘       │   │
    │                 │         │                    │              │   │
    │                 └─────────┼────────────────────┼──────────────┘   │
    │                           │                    │                  │
    │         ┌─────────────────┼────────────────────┼─────────────┐    │
    │         │                 ▼                    │             │    │
    │   ┌─────┴─────┐    ┌─────────────┐      ┌──────┴──────┐      │    │
    │   │   BAR0    │    │    BAR1     │      │  Crossbar   │      │    │
    │   │ Wishbone  │    │ DMA Buffer  │      │ Master Port │      │    │
    │   │  Bridge   │    │  Handler    │      └──────▲──────┘      │    │
    │   └─────┬─────┘    └──────┬──────┘             │             │    │
    │         │                 │                    │             │    │
    │         ▼                 ▼                    │             │    │
    │   ┌───────────┐    ┌─────────────┐      ┌──────┴──────┐      │    │
    │   │    BSA    │    │  DMA Buffer │◄────►│ DMA Engine  │      │    │
    │   │ Registers │    │   (16KB)    │      └─────────────┘      │    │
    │   └───────────┘    └─────────────┘                           │    │
    │                                                              │    │
    │   ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐       │
    │   │ MSI-X Table │  │  MSI-X PBA  │  │   MSI-X Controller  │       │
    │   │   (BAR2)    │  │   (BAR5)    │  └─────────────────────┘       │
    │   └─────────────┘  └─────────────┘                                │
    │                                                                   │
    │   ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐       │
    │   │ ATS Engine  │  │     ATC     │  │  ATS Invalidation   │       │
    │   └─────────────┘  └─────────────┘  │      Handler        │       │
    │                                     └─────────────────────┘       │
    │                                                                   │
    │   ┌─────────────────────┐                                         │
    │   │ Transaction Monitor │                                         │
    │   └─────────────────────┘                                         │
    │                                                                   │
    │   ┌─────────────────────┐                                         │
    │   │ PASID Prefix Inject │                                         │
    │   └─────────────────────┘                                         │
    └───────────────────────────────────────────────────────────────────┘

BAR Layout
----------

.. list-table::
   :header-rows: 1

   * - BAR
     - Size
     - Purpose
     - Handler
   * - BAR0
     - 4KB
     - CSR Registers
     - Wishbone Bridge → BSARegisters
   * - BAR1
     - 16KB
     - DMA Buffer
     - BSADMABufferHandler
   * - BAR2
     - 32KB
     - MSI-X Table (16 entries implemented)
     - LitePCIeMSIXTable
   * - BAR3
     - —
     - Disabled
     - Stub
   * - BAR4
     - —
     - Disabled
     - Stub
   * - BAR5
     - 4KB
     - MSI-X PBA (16 bits used)
     - LitePCIeMSIXPBA

Data Flow
---------

RX Path (Host → Exerciser)
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. PHY receives TLP from PCIe link
2. Depacketizer parses TLP header, extracts ``bar_hit`` field
3. BAR Dispatcher routes request to appropriate handler based on ``bar_hit``
4. Handler processes request:

   * BAR0: Wishbone bridge performs CSR read/write
   * BAR1: DMA buffer handler performs memory read/write
   * BAR2/5: MSI-X table/PBA access (lower entries only)
   * Transaction monitor taps the request stream for logging

5. Completion arbiter collects responses from all BARs
6. Packetizer formats completion TLP
7. PHY transmits completion

TX Path (Exerciser → Host)
~~~~~~~~~~~~~~~~~~~~~~~~~~

1. DMA engine or MSI-X controller initiates request
2. Master arbiter selects between pending requests
3. Packetizer formats request TLP
4. PASID prefix injector adds prefix if enabled
5. TX arbiter selects between main path and raw sources (ATS invalidation)
6. PHY transmits TLP

Key Components
--------------

See the following sections for detailed documentation:

* :doc:`endpoint` - Multi-BAR endpoint and routing
* :doc:`dma` - DMA engine and buffer
* :doc:`msix` - MSI-X subsystem
* :doc:`ats` - ATS engine, ATC, and invalidation
* :doc:`pasid` - PASID prefix injection
* :doc:`monitor` - Transaction monitoring
