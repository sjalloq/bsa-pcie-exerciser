PASID Prefix Injector
=====================

The ``PASIDPrefixInjector`` inserts E2E PASID TLP Prefixes into outbound
TLPs when required.

Source: ``src/bsa_pcie_exerciser/pasid/prefix_injector.py``

Overview
--------

PCIe PASID (Process Address Space ID) enables process-level isolation in
IOMMUs. When a TLP includes a PASID prefix, the IOMMU can apply per-process
translation tables.

The prefix is a 32-bit DWORD prepended to the TLP header:

::

    Without prefix: [HDR0|HDR1][HDR2|HDR3][DATA...]
    With prefix:    [PREFIX|HDR0][HDR1|HDR2][HDR3|DATA0][DATA...]

E2E PASID Prefix Format
-----------------------

.. list-table::
   :header-rows: 1

   * - Bits
     - Field
     - Description
   * - [31:24]
     - Type
     - ``0x91`` (E2E TLP Prefix for PASID)
   * - [23:22]
     - Reserved
     - 0
   * - [21]
     - PMR
     - Privileged Mode Requested
   * - [20]
     - Execute
     - Execute Requested
   * - [19:0]
     - PASID
     - 20-bit Process Address Space ID

Stream-Based Design
-------------------

PASID signals travel through the stream alongside TLP data via ``phy_layout``:

.. code-block:: python

    def phy_layout(data_width):
        return [
            ("dat", data_width),
            ("be", data_width//8),
            ("bar_hit", 6),
            ("pasid_en", 1),    # Enable PASID prefix
            ("pasid_val", 20),  # PASID value
            ("privileged", 1),  # PMR bit
            ("execute", 1),     # Execute bit
        ]

This eliminates timing races—the PASID decision travels with the TLP data.

Self-Latching Mechanism
-----------------------

On the first beat of a TLP, the injector captures PASID signals:

.. code-block:: python

    fsm.act("IDLE",
        sink.ready.eq(1),
        If(sink.valid & sink.first,
            # Capture PASID signals from stream
            NextValue(captured_pasid_en, sink.pasid_en),
            NextValue(captured_prefix, prefix_dword),
            # Buffer the first beat
            NextValue(buffered_first_dat, sink.dat),
            NextValue(buffered_first_be, sink.be),
            NextValue(buffered_first_last, sink.last),
            NextState("DECIDE"),
        ),
    )

In the DECIDE state, the captured values determine the path:

.. code-block:: python

    fsm.act("DECIDE",
        sink.ready.eq(0),  # Hold input
        If(captured_pasid_en,
            # With prefix: shift data
            ...
            NextState("SHIFT"),
        ).Else(
            # No prefix: pass through
            ...
            NextState("PASSTHROUGH"),
        ),
    )

FSM States
----------

::

    IDLE ──► DECIDE ──► PASSTHROUGH ──► IDLE
                  │
                  └──► SHIFT ──► FLUSH ──► IDLE

* **IDLE**: Wait for first beat, capture PASID signals
* **DECIDE**: Determine prefix/no-prefix path (race-free)
* **PASSTHROUGH**: Forward beats unchanged (no prefix)
* **SHIFT**: Shift data by one DWORD position (with prefix)
* **FLUSH**: Output final partial beat after shifting

Data Shifting
-------------

When a prefix is added, all data shifts by 32 bits:

**First beat output (DECIDE state)**:

::

    Output: [PREFIX | HDR0]
    Buffer: HDR1 (upper 32 bits of original first beat)

**Subsequent beats (SHIFT state)**:

::

    Output: [buffered | lower 32 of current]
    Buffer: upper 32 of current

**Final beat (FLUSH state)**:

::

    Output: [buffered | padding]

Integration
-----------

The injector sits between packetizer and PHY in the TX path:

.. code-block:: python

    if tx_filter is not None:
        self.comb += self.packetizer.source.connect(tx_filter.sink)
        main_tx_source = tx_filter.source
    else:
        main_tx_source = self.packetizer.source

DMA and ATS engines set PASID fields on their source streams:

.. code-block:: python

    self.comb += [
        source.pasid_en.eq(current_pasid_en),
        source.pasid_val.eq(current_pasid_val),
        source.privileged.eq(current_priv),
        source.execute.eq(current_exec),
    ]

The packetizer passes these through to ``phy_layout``, and the injector
consumes them to decide whether to add a prefix.
