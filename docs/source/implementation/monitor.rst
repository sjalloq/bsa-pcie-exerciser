Transaction Monitor
===================

The ``TransactionMonitor`` captures incoming PCIe transactions for software
inspection, enabling verification of request ordering and data payloads.

Source: ``src/bsa_pcie_exerciser/gateware/monitor/txn_monitor.py``

Overview
--------

The monitor taps into the request stream from the depacketizer and captures
each data beat into a FIFO. Software reads the FIFO via CSR registers.

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

Transaction Record Format
-------------------------

Each captured beat produces a 5-word (32-bit) record:

.. list-table::
   :header-rows: 1

   * - Word
     - Name
     - Description
   * - 0
     - TX_ATTRIBUTES
     - [0]=type (cfg only), [1]=read, [2]=cfg, [31:16]=byte size one-hot
   * - 1
     - ADDRESS[31:0]
     - Lower 32 bits of address (CFG or MEM)
   * - 2
     - ADDRESS[63:32]
     - Upper 32 bits of address
   * - 3
     - DATA[31:0]
     - Lower 32 bits of data
   * - 4
     - DATA[63:32]
     - Upper 32 bits of data

The byte size field uses a one-hot encoding of log2(bytes). For example,
1 byte sets bit 0, 2 bytes sets bit 1, 4 bytes sets bit 2, and 8 bytes
sets bit 3. Each beat is recorded as a separate transaction entry.

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

* **TXN_CTRL[0]**: Enable monitoring
* **TXN_CTRL[1]**: Clear FIFO (write 1 to clear)
* **TXN_TRACE**: Read captured transaction (auto-advances FIFO)

Usage Example
-------------

1. Software enables monitor: ``TXNCTL = 0x01``
2. Software triggers DMA or other operation
3. Monitor captures incoming TLPs to FIFO
4. Software reads ``TXNDATA`` repeatedly until empty
5. Software verifies ordering, size, and data payloads match expectations

USB TLP Monitor (FT601)
-----------------------

In addition to the BAR0 ``TXN_TRACE`` monitor, the USB monitor subsystem
captures full TLPs (RX and TX) and streams them over USB channel 1.

Source: ``src/bsa_pcie_exerciser/gateware/usb/monitor/``

Key points:

* Captures both inbound and outbound traffic with full payloads.
* Uses separate header/payload FIFOs and reports dropped/truncated counts.
* Controlled via ``USB_MON_CTL`` and counters in BAR0.
* Packet format is defined in ``bsa_pcie_exerciser.common.protocol`` and
  summarized in :doc:`platforms/ft601`.

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
