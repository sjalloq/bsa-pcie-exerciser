ATS Subsystem
=============

The Address Translation Services implementation consists of:

* **ATSEngine**: Generates Translation Request TLPs, parses responses
* **ATC**: Address Translation Cache for storing translations
* **ATSInvalidationHandler**: Handles invalidation requests from IOMMU

Source: ``src/bsa_pcie_exerciser/gateware/ats/``

ATS Engine
----------

Source: ``src/bsa_pcie_exerciser/gateware/ats/engine.py``

The ``ATSEngine`` issues ATS Translation Request TLPs and parses completions.

Operation
~~~~~~~~~

1. Software triggers translation request via ``ATSCTL``
2. ATS ECAP control ``ATS_ENABLE`` must be set (bit 31) to allow requests
3. Engine issues Translation Request TLP
4. Engine receives Translation Completion
5. Engine extracts translated address, permissions, range
6. Results written to status registers and optionally to ATC

Control Interface
~~~~~~~~~~~~~~~~~

.. code-block:: python

    self.trigger     = Signal()    # Start translation request
    self.address     = Signal(64)  # Address to translate
    self.pasid_en    = Signal()    # Include PASID
    self.pasid_val   = Signal(20)  # PASID value
    self.no_write    = Signal()    # Request read-only permission
    self.exec_req    = Signal()    # Request execute permission

Status Interface
~~~~~~~~~~~~~~~~

.. code-block:: python

    self.in_flight       = Signal()    # Request in progress
    self.success         = Signal()    # Translation succeeded
    self.translated_addr = Signal(64)  # Result address
    self.range_size      = Signal(32)  # Translation range
    self.permissions     = Signal(3)   # R/W/X permissions

Address Translation Cache (ATC)
-------------------------------

Source: ``src/bsa_pcie_exerciser/gateware/ats/atc.py``

The ``ATC`` caches a single translation entry. This simplified design is
sufficient for BSA compliance testing.

Cache Entry
~~~~~~~~~~~

.. code-block:: python

    self._input_addr   = Signal(64)   # Untranslated address
    self._output_addr  = Signal(64)   # Translated address
    self._range_size   = Signal(32)   # Range size
    self._permissions  = Signal(3)    # R/W/X
    self._pasid_valid  = Signal()     # Has PASID
    self._pasid_val    = Signal(20)   # PASID value
    self.valid         = Signal()     # Entry valid

Lookup Interface
~~~~~~~~~~~~~~~~

The DMA engine uses the lookup interface:

.. code-block:: python

    # Inputs
    self.lookup_addr        = Signal(64)
    self.lookup_pasid_valid = Signal()
    self.lookup_pasid_val   = Signal(20)

    # Output
    self.lookup_hit    = Signal()   # Address in range AND PASID matches
    self.lookup_output = Signal(64) # Translated address

Lookup logic checks:

1. Entry is valid
2. Input address is within cached range
3. PASID matches (if applicable)

Store Interface
~~~~~~~~~~~~~~~

The ATS engine stores translations after successful requests:

.. code-block:: python

    self.store              = Signal()    # Trigger store
    self.store_input_addr   = Signal(64)
    self.store_output_addr  = Signal(64)
    self.store_range_size   = Signal(32)
    self.store_permissions  = Signal(3)
    self.store_pasid_valid  = Signal()
    self.store_pasid_val    = Signal(20)

ATS Invalidation Handler
------------------------

Source: ``src/bsa_pcie_exerciser/gateware/ats/invalidation.py``

The ``ATSInvalidationHandler`` processes ATS Invalidation Requests from
the host IOMMU and sends Invalidation Completion responses.

Why It Bypasses the Packetizer
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

ATS Invalidation Completion is a **Message TLP**, not a Completion TLP:

* Format/Type: ``001 10010`` (4DW header, Message routed by ID)
* Message Code: ``0x02`` (Invalidation Completion)

The standard packetizer only handles Memory Read/Write and Completion TLPs.
Message TLPs have a different header format, so the invalidation handler
constructs the raw TLP directly:

.. code-block:: python

    # DW0: Fmt=001 (4DW no data), Type=10010 (Message routed by ID)
    msg_dw0.eq(
        (0b001 << 29) |      # Fmt = 001 (4DW, no data)
        (0b10010 << 24)      # Type = 10010 (Message routed by ID)
    ),
    # DW1: [Requester ID:16][Tag:8][Message Code:8]
    msg_dw1.eq(
        (phy.id << 16) |
        (inv_tag << 8) |
        0x02                 # Message Code = Invalidation Completion
    ),

TX Path Integration
~~~~~~~~~~~~~~~~~~~

The handler's ``msg_source`` is passed to the endpoint as a raw TX source:

.. code-block:: python

    self.pcie_endpoint = LitePCIeMultiBAREndpoint(...,
        raw_tx_sources = [self.ats_invalidation.msg_source],
    )

This path bypasses both the packetizer and PASID injector:

::

    packetizer → PASID injector → TX arbiter → PHY
                                       ↑
    ATS Inv Handler (msg_source) ──────┘

FSM States
~~~~~~~~~~

::

    IDLE ──► RECEIVE ──► CHECK ──► WAIT_ATS ──► INVALIDATE ──► SEND_CPL
                           │          ▲              ▲            │
                           │          │              │            │
                           └──► WAIT_DMA ────────────┘            │
                           │                                      │
                           └──────────────────────────────────────┘
                               (no overlap or ATC empty)

Invalidation Coordination
~~~~~~~~~~~~~~~~~~~~~~~~~

The handler coordinates with other components:

1. **ATC Check**: Does invalidation range overlap with cached entry?
2. **PASID Check**: Does PASID match (or is it global)?
3. **ATS Engine**: If translation in flight, signal retry
4. **DMA Engine**: If DMA using ATC, wait for completion
5. **Invalidate**: Clear ATC entry
6. **Respond**: Send Invalidation Completion message

.. code-block:: python

    fsm.act("CHECK",
        If(~self.atc_valid,
            NextState("SEND_CPL"),  # Nothing to invalidate
        ).Elif(~should_invalidate,
            NextState("SEND_CPL"),  # No overlap
        ).Elif(self.ats_in_flight,
            self.ats_retry.eq(1),   # Tell ATS engine to retry
            NextState("WAIT_ATS"),
        ).Elif(self.dma_busy & self.dma_using_atc,
            NextState("WAIT_DMA"),  # Wait for DMA
        ).Else(
            NextState("INVALIDATE"),
        ),
    )
