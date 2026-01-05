MSI-X Subsystem
===============

The MSI-X implementation consists of three components:

* **LitePCIeMSIXTable**: BAR2 handler for MSI-X table storage
* **LitePCIeMSIXPBA**: BAR5 handler for Pending Bit Array
* **LitePCIeMSIXController**: TLP generator for interrupt delivery

Source: ``src/bsa_pcie_exerciser/gateware/msix/``

MSI-X Table
-----------

Source: ``src/bsa_pcie_exerciser/gateware/msix/table.py``

The ``LitePCIeMSIXTable`` provides PCIe-accessible storage for 16 MSI-X
vectors (256 bytes) mapped at the base of the BAR2 window. The BAR2 window
remains 32KB for compatibility; entries beyond vector 15 are reserved.

Memory Layout
~~~~~~~~~~~~~

Each vector entry is 16 bytes (4 DWORDs):

.. list-table::
   :header-rows: 1

   * - Offset
     - Field
     - Description
   * - 0x00
     - Message Address Low
     - Target address [31:0]
   * - 0x04
     - Message Address High
     - Target address [63:32]
   * - 0x08
     - Message Data
     - Data written to trigger interrupt
   * - 0x0C
     - Vector Control
     - Bit 0 = Mask

Internally stored as 64-bit QWORDs:

* QWORD 0: ``{addr_hi, addr_lo}``
* QWORD 1: ``{control, msg_data}``

Dual-Port Access
~~~~~~~~~~~~~~~~

The table uses dual-port memory:

* **Port A**: PCIe access (host reads/writes)
* **Port B**: Internal read (for controller)

FSM States
~~~~~~~~~~

::

    IDLE ──► READ_ADDR ──► READ_DATA ──► COMPLETE
       │
       └──► WRITE ──► IDLE

The FSM handles PCIe Memory Read/Write requests and generates completions.

Internal Read Interface
~~~~~~~~~~~~~~~~~~~~~~~

The controller reads table entries via dedicated signals:

.. code-block:: python

    self.vector_num = Signal(11)   # Which vector (0-15 used)
    self.read_en    = Signal()     # Trigger read
    self.read_valid = Signal()     # Data valid (after 3 cycles)
    self.msg_addr   = Signal(64)   # Message Address
    self.msg_data   = Signal(32)   # Message Data
    self.masked     = Signal()     # Vector masked?

MSI-X PBA
---------

Source: ``src/bsa_pcie_exerciser/gateware/msix/table.py``

The ``LitePCIeMSIXPBA`` manages the Pending Bit Array:

* 16 bits stored in a single QWORD (8 bytes)
* Read-only from PCIe perspective
* Set/clear internally by controller

Internal Interface
~~~~~~~~~~~~~~~~~~

.. code-block:: python

    self.set_pending   = Signal()   # Pulse to set pending bit
    self.clear_pending = Signal()   # Pulse to clear pending bit
    self.vector_num    = Signal(11) # Which vector to modify (0-15 used)

MSI-X Controller
----------------

Source: ``src/bsa_pcie_exerciser/msix/controller.py``

The ``LitePCIeMSIXController`` generates Memory Write TLPs for interrupts.

Operation
~~~~~~~~~

When software triggers a vector:

1. Controller reads table entry via Port B
2. If masked: Sets PBA pending bit, returns to IDLE
3. If unmasked: Issues Memory Write TLP to message address

FSM States
~~~~~~~~~~

::

    IDLE ──► READ_TABLE ──► ISSUE_WRITE ──► IDLE
                   │
                   └──► (masked) ──► IDLE

TLP Generation
~~~~~~~~~~~~~~

The controller generates a single-beat Memory Write:

.. code-block:: python

    self.comb += [
        port.source.we.eq(1),           # Write request
        port.source.adr.eq(table.msg_addr),
        port.source.len.eq(1),          # 1 DWORD
        port.source.dat.eq(table.msg_data),
    ]

MSI-X uses posted writes—no completion is expected. The completion sink
is tied off:

.. code-block:: python

    # Unused completion sink - MSI-X uses only posted writes
    self.comb += port.sink.ready.eq(1)

Software Trigger Flow
---------------------

1. Software writes vector number to ``MSICTL[10:0]``
2. Software sets trigger bit ``MSICTL[31]``
3. Controller latches vector, starts table read
4. If unmasked: Memory Write TLP sent
5. If masked: PBA bit set
6. Trigger bit self-clears once accepted
