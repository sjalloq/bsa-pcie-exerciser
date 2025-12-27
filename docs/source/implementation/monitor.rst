Transaction Monitor
===================

The ``TransactionMonitor`` captures incoming PCIe transactions for software
inspection, enabling verification of TLP attributes and request parameters.

Source: ``src/bsa_pcie_exerciser/monitor/txn_monitor.py``

Overview
--------

The monitor taps into the request stream from the depacketizer and captures
transaction metadata into a FIFO. Software can read captured transactions
via control registers.

Architecture
------------

::

    Depacketizer
         │
         │ req_source
         ▼
    ┌─────────────────┐
    │  Tap Point      │──────────────────────────► BAR Dispatcher
    │  (valid&ready)  │
    └────────┬────────┘
             │
             ▼
    ┌─────────────────┐
    │    Monitor      │
    │                 │
    │  ┌───────────┐  │
    │  │   FIFO    │  │◄──── Software reads via TXNDATA
    │  │ (32 deep) │  │
    │  └───────────┘  │
    │                 │
    └─────────────────┘

Tap Point
---------

The monitor observes transactions without affecting the data path:

.. code-block:: python

    req_source = self.pcie_endpoint.req_source
    self.comb += [
        self.txn_monitor.tap_valid.eq(req_source.valid & req_source.ready),
        self.txn_monitor.tap_first.eq(req_source.first),
        self.txn_monitor.tap_last.eq(req_source.last),
        self.txn_monitor.tap_we.eq(req_source.we),
        self.txn_monitor.tap_adr.eq(req_source.adr),
        self.txn_monitor.tap_len.eq(req_source.len),
        # ... other fields ...
    ]

Captured Fields
---------------

Each FIFO entry (64 bits) contains:

.. list-table::
   :header-rows: 1

   * - Bits
     - Field
     - Description
   * - [0]
     - we
     - Write enable (1=write, 0=read)
   * - [6:1]
     - bar_hit
     - BAR hit field
   * - [8:7]
     - attr
     - TLP attributes (No-Snoop, RO)
   * - [10:9]
     - at
     - Address Type field
   * - [14:11]
     - first_be
     - First DWORD byte enables
   * - [18:15]
     - last_be
     - Last DWORD byte enables
   * - [28:19]
     - len
     - Length in DWORDs
   * - [36:29]
     - tag
     - Transaction tag
   * - [52:37]
     - req_id
     - Requester ID
   * - [63:53]
     - Reserved
     -

Address is captured separately or in subsequent FIFO entries depending
on implementation.

Control Interface
-----------------

.. code-block:: python

    self.enable = Signal()      # Enable capture
    self.clear  = Signal()      # Clear FIFO

    self.fifo_data  = Signal(64)  # Data output
    self.fifo_empty = Signal()    # FIFO empty flag
    self.fifo_read  = Signal()    # Read strobe

Software Interface
------------------

Via BSA registers:

* **TXNCTL[0]**: Enable monitoring
* **TXNCTL[1]**: Clear FIFO (write 1 to clear)
* **TXNDATA**: Read captured transaction (auto-advances FIFO)

Usage Example
-------------

1. Software enables monitor: ``TXNCTL = 0x01``
2. Software triggers DMA or other operation
3. Monitor captures incoming TLPs to FIFO
4. Software reads ``TXNDATA`` repeatedly until empty
5. Software verifies TLP attributes match expectations

Use Cases
---------

SMMU/IOMMU Testing
~~~~~~~~~~~~~~~~~~

Verify that DMA requests have correct attributes:

* No-Snoop bit set/clear as configured
* Address Type field matches expectation
* PASID prefix present (visible in separate monitoring)

Interrupt Testing
~~~~~~~~~~~~~~~~~

Capture MSI-X write transactions to verify:

* Correct target address
* Correct message data
* Proper transaction format

Debug
~~~~~

Diagnose issues by inspecting actual TLP traffic:

* Verify BAR routing
* Check request parameters
* Identify unexpected transactions
