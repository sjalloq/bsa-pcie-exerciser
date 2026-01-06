BAR0 Register Map
=================

The BSA Exerciser exposes control and status registers via BAR0 per the
ARM BSA Exerciser specification. Additional USB Monitor registers are
provided for the Squirrel/CaptainDMA platform.

.. note::

   Implementation: ``src/bsa_pcie_exerciser/gateware/core/bsa_registers.py``

   Reference: ``external/sysarch-acs/docs/pcie/Exerciser.md``

Register Summary
----------------

.. list-table:: BAR0 Register Map
   :header-rows: 1
   :widths: 10 25 10 55

   * - Offset
     - Name
     - Access
     - Description
   * - 0x000
     - MSICTL
     - RW
     - MSI-X trigger control
   * - 0x004
     - INTXCTL
     - RW
     - Legacy interrupt control
   * - 0x008
     - DMACTL
     - RW
     - DMA control (trigger, direction, attributes)
   * - 0x00C
     - DMA_OFFSET
     - RW
     - DMA buffer offset within BAR1
   * - 0x010
     - DMA_BUS_ADDR_LO
     - RW
     - DMA bus address [31:0]
   * - 0x014
     - DMA_BUS_ADDR_HI
     - RW
     - DMA bus address [63:32]
   * - 0x018
     - DMA_LEN
     - RW
     - DMA transfer length in bytes
   * - 0x01C
     - DMASTATUS
     - RW
     - DMA status
   * - 0x020
     - PASID_VAL
     - RW
     - PASID value for DMA/ATS operations
   * - 0x024
     - ATSCTL
     - RW
     - ATS control and status
   * - 0x028
     - ATS_ADDR_LO
     - RO
     - ATS translated address [31:0]
   * - 0x02C
     - ATS_ADDR_HI
     - RO
     - ATS translated address [63:32]
   * - 0x030
     - ATS_RANGE_SIZE
     - RO
     - ATS translated range size in bytes
   * - 0x038
     - ATS_PERM
     - RO
     - ATS reply permissions
   * - 0x03C
     - RID_CTL
     - RW
     - Requester ID override control
   * - 0x040
     - TXN_TRACE
     - RO
     - Transaction trace FIFO read
   * - 0x044
     - TXN_CTRL
     - RW
     - Transaction monitor control
   * - 0x048
     - ID
     - RO
     - Exerciser identification

User Extended Config Space (ECAPs)
----------------------------------

The PCIe core forwards configuration requests at or above
``EXT_PCI_CFG_Space_Addr`` (DWORD address) to user logic. The exerciser uses
this region to advertise ATS/PASID/ACS/DPC ECAPs plus a DVSEC for error
injection. The ECAP offsets are design-defined; software follows the ECAP
linked list via the Next Pointer fields rather than assuming fixed addresses.

Default layout (DWORD addresses, base = ``0x6B`` / byte 0x1AC):

.. code-block:: text

   Core ECAPs (fixed by PCIe IP)
     ... -> last_core_ecap -> next = 0x6B

   User ECAP chain (BSA exerciser)
     0x6B: ATS  ECAP header
     0x6C: ATS  control
     0x6D: PASID ECAP header
     0x6E: PASID capability/control
     0x6F: ACS  ECAP header
     0x70: ACS  control
     0x71: DPC  ECAP header
     0x72: DPC  control
     0x73: DPC  status
     0x74: DVSEC header
     0x75: DVSEC header 1
     0x76: DVSEC control (error injection / poison)
     0x77: Next = 0 (end of list)

The user ECAP chain is implemented in
``src/bsa_pcie_exerciser/gateware/config/pcie_config.py``.

DVSEC Error Injection Control
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The DVSEC control DWORD (``0x76``) provides error injection and poison mode:

.. list-table::
   :header-rows: 1
   :widths: 20 30 50

   * - Bits
     - Name
     - Description
   * - 15:0
     - DVSEC_ID
     - Read-only, fixed to ``0x0001``.
   * - 16
     - INJECT_ON_DMA
     - Inject error when DMA triggers (future use).
   * - 17
     - INJECT_NOW
     - Write 1 to trigger an immediate error injection (self-clearing).
   * - 18
     - POISON_MODE
     - Force BAR0/BAR1 reads to return all 1s and ignore writes.
   * - 30:20
     - ERROR_CODE
     - Error code mapped to PCIe core ``cfg_err_*``.
   * - 31
     - FATAL
     - Marks injected error as fatal for DPC status reporting.

Error codes drive the PCIe core error inputs. The mapping is defined in
``src/bsa_pcie_exerciser/gateware/soc/base.py``.

USB Monitor Registers (Squirrel/CaptainDMA only):

.. list-table::
   :header-rows: 1
   :widths: 10 25 10 55

   * - Offset
     - Name
     - Access
     - Description
   * - 0x080
     - USB_MON_CTRL
     - RW
     - USB monitor control
   * - 0x084
     - USB_MON_STATUS
     - RO
     - USB monitor status (reserved)
   * - 0x088
     - USB_MON_RX_CAPTURED
     - RO
     - RX packets captured count
   * - 0x08C
     - USB_MON_RX_DROPPED
     - RO
     - RX packets dropped count
   * - 0x090
     - USB_MON_TX_CAPTURED
     - RO
     - TX packets captured count
   * - 0x094
     - USB_MON_TX_DROPPED
     - RO
     - TX packets dropped count
   * - 0x098
     - USB_MON_RX_TRUNCATED
     - RO
     - RX packets truncated count
   * - 0x09C
     - USB_MON_TX_TRUNCATED
     - RO
     - TX packets truncated count

----

MSI Control (0x000)
-------------------

Configure and trigger MSI-X interrupts.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [10:0]
     - vector_id
     - RW
     - MSI-X vector index (0-15 used)
   * - [30:11]
     - reserved
     - RO
     - Reserved
   * - [31]
     - trigger
     - RW
     - Write 1 to generate MSI-X. Self-clearing after MSI sent.

**Usage:** Poll until trigger=0, then write with trigger=1 and desired vector_id.

----

Legacy Interrupt Control (0x004)
--------------------------------

Control legacy INTx assertion.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [0]
     - assert
     - RW
     - 1 = Assert INTx, 0 = Deassert
   * - [31:1]
     - reserved
     - RO
     - Reserved

----

DMA Control (0x008)
-------------------

Configure and trigger DMA transactions.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [3:0]
     - trigger
     - RW
     - Write 0x1 to start DMA. Self-clearing. Values 0x2-0xF reserved.
   * - [4]
     - direction
     - RW
     - 0 = Read from host, 1 = Write to host
   * - [5]
     - no_snoop
     - RW
     - 0 = Snoop, 1 = No-Snoop attribute
   * - [6]
     - pasid_en
     - RW
     - 1 = Include PASID TLP prefix
   * - [7]
     - privileged
     - RW
     - 1 = Privileged access mode (requires pasid_en=1)
   * - [8]
     - instruction
     - RW
     - 1 = Instruction access (requires pasid_en=1)
   * - [9]
     - use_atc
     - RW
     - 1 = Use ATC for address translation
   * - [11:10]
     - addr_type
     - RW
     - 0 = Default/Untranslated, 1 = Untranslated, 2 = Translated, 3 = Reserved
   * - [31:12]
     - reserved
     - RO
     - Reserved

----

DMA Status (0x01C)
------------------

Status of last DMA transaction.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [1:0]
     - status
     - RO
     - 0 = OK, 1 = Range error, 2 = Internal error/Timeout, 3 = Reserved
   * - [2]
     - clear
     - WO
     - Write 1 to clear status
   * - [31:3]
     - reserved
     - RO
     - Reserved

----

PASID Value (0x020)
-------------------

PASID value for DMA and ATS operations.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [19:0]
     - pasid
     - RW
     - 20-bit PASID value
   * - [31:20]
     - reserved
     - RO
     - Reserved

----

ATS Control (0x024)
-------------------

Control ATS translation requests and view status.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [0]
     - trigger
     - WO
     - Write 1 to send ATS request. Self-clearing.
   * - [1]
     - privileged
     - RW
     - 1 = Privileged access (requires pasid_en=1)
   * - [2]
     - no_write
     - RW
     - 1 = Request read-only permission
   * - [3]
     - pasid_en
     - RW
     - 1 = Include PASID in ATS request
   * - [4]
     - exec_req
     - RW
     - 1 = Request execute permission (requires pasid_en=1)
   * - [5]
     - clear_atc
     - W1C
     - Write 1 to clear ATC. Self-clearing.
   * - [6]
     - in_flight
     - RO
     - 1 = ATS request in progress
   * - [7]
     - success
     - RO
     - 1 = Translation successful
   * - [8]
     - cacheable
     - RO
     - 1 = Result is cacheable (RW != 0)
   * - [9]
     - invalidated
     - RO/W1C
     - 1 = ATC was invalidated. Write 1 to clear.
   * - [31:10]
     - reserved
     - RO
     - Reserved

----

ATS Permissions (0x038)
-----------------------

Permissions returned from ATS translation.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [0]
     - exec
     - RO
     - Execute permission granted
   * - [1]
     - write
     - RO
     - Write permission granted
   * - [2]
     - read
     - RO
     - Read permission granted
   * - [3]
     - exec_priv
     - RO
     - Execute permission (privileged)
   * - [4]
     - write_priv
     - RO
     - Write permission (privileged)
   * - [5]
     - reserved
     - RO
     - Reserved
   * - [6]
     - read_priv
     - RO
     - Read permission (privileged)
   * - [31:7]
     - reserved
     - RO
     - Reserved

----

Requester ID Control (0x03C)
----------------------------

Override requester ID for DMA transactions.

.. warning::

   This register enables illegal transactions (per PCIe spec) for ACS
   source validation testing.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [15:0]
     - req_id
     - RW
     - Custom Requester ID value
   * - [30:16]
     - reserved
     - RO
     - Reserved
   * - [31]
     - valid
     - RW
     - 1 = Use custom Requester ID

----

Transaction Trace (0x040)
-------------------------

Read captured transactions from FIFO.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [31:0]
     - data
     - RO
     - Next FIFO word. Returns 0xFFFFFFFF when empty.

**Transaction Record Format** (5 words per transaction beat):

1. TX_ATTRIBUTES: [0]=type, [1]=R/W, [2]=CFG/MEM, [31:16]=byte size one-hot (log2)
2. ADDRESS[31:0]
3. ADDRESS[63:32]
4. DATA[31:0]
5. DATA[63:32]

The byte size field uses a one-hot encoding of log2(bytes) (e.g., 1B -> bit0,
2B -> bit1, 4B -> bit2, 8B -> bit3). Each beat is recorded as a separate entry.

----

Transaction Control (0x044)
---------------------------

Control transaction monitoring.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [0]
     - enable
     - RW
     - 1 = Capture transactions
   * - [1]
     - clear
     - W1C
     - Write 1 to clear FIFO. Self-clearing.
   * - [2]
     - overflow
     - RO
     - 1 = FIFO overflow occurred
   * - [15:8]
     - count
     - RO
     - Number of entries in FIFO
   * - [31:16]
     - reserved
     - RO
     - Reserved

----

Exerciser ID (0x048)
--------------------

Device identification.

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [15:0]
     - vendor_id
     - RO
     - 0x13B5 (ARM)
   * - [31:16]
     - device_id
     - RO
     - 0xED01

Combined value: 0xED0113B5

----

USB Monitor Control (0x080)
---------------------------

Control USB TLP monitoring (Squirrel/CaptainDMA only).

.. list-table::
   :header-rows: 1
   :widths: 10 15 10 65

   * - Bits
     - Name
     - Access
     - Description
   * - [0]
     - rx_enable
     - RW
     - 1 = Capture RX (inbound) TLPs
   * - [1]
     - tx_enable
     - RW
     - 1 = Capture TX (outbound) TLPs
   * - [2]
     - clear_stats
     - W1C
     - Write 1 to clear all statistics. Self-clearing.
   * - [31:3]
     - reserved
     - RO
     - Reserved

**Default:** 0x03 (both RX and TX enabled)

----

USB Monitor Statistics (0x088-0x09C)
------------------------------------

Read-only counters for USB monitor diagnostics.

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Offset
     - Register
     - Description
   * - 0x088
     - USB_MON_RX_CAPTURED
     - Count of RX packets successfully captured
   * - 0x08C
     - USB_MON_RX_DROPPED
     - Count of RX packets dropped (FIFO full)
   * - 0x090
     - USB_MON_TX_CAPTURED
     - Count of TX packets successfully captured
   * - 0x094
     - USB_MON_TX_DROPPED
     - Count of TX packets dropped (FIFO full)
   * - 0x098
     - USB_MON_RX_TRUNCATED
     - Count of RX packets truncated (exceeded max size)
   * - 0x09C
     - USB_MON_TX_TRUNCATED
     - Count of TX packets truncated (exceeded max size)

----

Usage Examples
--------------

Trigger MSI-X Vector 5
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: c

   // Write vector ID with trigger bit
   write32(BAR0 + 0x000, (1 << 31) | 5);

DMA Read from Host
~~~~~~~~~~~~~~~~~~

.. code-block:: c

   // Configure transfer
   write32(BAR0 + 0x010, host_addr & 0xFFFFFFFF);  // Bus address low
   write32(BAR0 + 0x014, host_addr >> 32);          // Bus address high
   write32(BAR0 + 0x018, 256);                      // 256 bytes
   write32(BAR0 + 0x00C, 0);                        // Buffer offset 0

   // Start DMA read (direction=0, trigger=1)
   write32(BAR0 + 0x008, 0x01);

   // Poll for completion
   while ((read32(BAR0 + 0x01C) & 0x3) == 0)
       ;

DMA Write with No-Snoop and PASID
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: c

   // Set PASID value
   write32(BAR0 + 0x020, 0x42);

   // Configure transfer
   write32(BAR0 + 0x010, host_addr & 0xFFFFFFFF);
   write32(BAR0 + 0x014, host_addr >> 32);
   write32(BAR0 + 0x018, 128);
   write32(BAR0 + 0x00C, 0x100);

   // Start DMA write with no-snoop + PASID
   // direction=1, no_snoop=1, pasid_en=1, trigger=1
   write32(BAR0 + 0x008, 0x71);

ATS Translation Request
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: c

   // Set untranslated address
   write32(BAR0 + 0x010, virt_addr & 0xFFFFFFFF);
   write32(BAR0 + 0x014, virt_addr >> 32);

   // Trigger ATS request
   write32(BAR0 + 0x024, 0x01);

   // Wait for completion
   while (read32(BAR0 + 0x024) & (1 << 6))
       ;

   // Check success and read result
   if (read32(BAR0 + 0x024) & (1 << 7)) {
       uint64_t translated = read32(BAR0 + 0x028) |
                            ((uint64_t)read32(BAR0 + 0x02C) << 32);
   }
