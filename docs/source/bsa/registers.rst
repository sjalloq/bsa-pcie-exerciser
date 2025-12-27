Register Requirements
=====================

The BSA Exerciser exposes control and status registers via BAR0 for
software to configure and monitor exerciser operations.

Register Map Overview
---------------------

The register space is organized as follows:

.. list-table:: Register Map
   :header-rows: 1
   :widths: 15 20 65

   * - Offset
     - Name
     - Description
   * - 0x00
     - DMACTL
     - DMA control (trigger, direction, attributes)
   * - 0x04
     - INTXCTL
     - Legacy INTx control
   * - 0x08
     - DMASTATUS
     - DMA status (busy, completion status)
   * - 0x10
     - DMA_BUS_ADDR_LO
     - DMA target address [31:0]
   * - 0x14
     - DMA_BUS_ADDR_HI
     - DMA target address [63:32]
   * - 0x18
     - DMA_LEN
     - DMA transfer length in bytes
   * - 0x1C
     - DMA_OFFSET
     - Offset within internal buffer
   * - 0x20
     - MSICTL
     - MSI-X trigger control
   * - 0x30
     - ATSCTL
     - ATS control (trigger, flags)
   * - 0x34
     - ATSSTATUS
     - ATS status (in-flight, success, etc.)
   * - 0x38
     - ATS_ADDR_LO
     - ATS translated address [31:0]
   * - 0x3C
     - ATS_ADDR_HI
     - ATS translated address [63:32]
   * - 0x40
     - PASID
     - PASID value for DMA/ATS operations
   * - 0x50
     - TXNCTL
     - Transaction monitor control
   * - 0x54
     - TXNDATA
     - Transaction monitor FIFO data

DMA Control Registers
---------------------

DMACTL (0x00)
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Bits
     - Name
     - Description
   * - [0]
     - TRIGGER
     - Write 1 to start DMA (self-clearing)
   * - [1]
     - DIRECTION
     - 0=Read from host, 1=Write to host
   * - [2]
     - NO_SNOOP
     - Set No-Snoop attribute in TLP
   * - [4:3]
     - ADDR_TYPE
     - Address Type field for TLP
   * - [5]
     - USE_ATC
     - Use ATC for address translation
   * - [8]
     - PASID_EN
     - Enable PASID TLP prefix
   * - [9]
     - PRIVILEGED
     - Privileged Mode Requested
   * - [10]
     - INSTRUCTION
     - Execute/Instruction access

DMASTATUS (0x08)
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Bits
     - Name
     - Description
   * - [0]
     - BUSY
     - DMA operation in progress
   * - [2:1]
     - STATUS
     - 00=OK, 01=Error, 10=Timeout
   * - [2]
     - CLEAR
     - Write 1 to clear status

ATS Control Registers
---------------------

ATSCTL (0x30)
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Bits
     - Name
     - Description
   * - [0]
     - TRIGGER
     - Write 1 to start translation request
   * - [1]
     - NO_WRITE
     - Request read-only permission
   * - [2]
     - PASID_EN
     - Enable PASID for translation
   * - [3]
     - PRIVILEGED
     - Privileged access request
   * - [4]
     - EXEC_REQ
     - Execute permission request
   * - [8]
     - CLEAR_ATC
     - Write 1 to clear ATC

ATSSTATUS (0x34)
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1

   * - Bits
     - Name
     - Description
   * - [0]
     - IN_FLIGHT
     - Translation request in progress
   * - [1]
     - SUCCESS
     - Last translation succeeded
   * - [2]
     - CACHEABLE
     - Translation is cacheable
   * - [3]
     - INVALIDATED
     - ATC was invalidated
