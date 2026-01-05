.. _pcie-msix:

=======================
MSI-X Interrupts
=======================

This document explains MSI-X (Message Signaled Interrupts - Extended) in the
context of PCIe and the BSA Exerciser implementation.

.. contents:: Table of Contents
   :local:
   :depth: 2

Background
==========

The Problem with Legacy Interrupts
----------------------------------

Traditional PCI used dedicated interrupt pins (INTA#, INTB#, INTC#, INTD#) -
physical wires shared across multiple devices. This approach had several
limitations:

- **Limited interrupts**: Only 4 interrupt lines for all devices on a bus
- **Interrupt sharing**: Multiple devices could be connected to the same line,
  requiring the CPU to poll each device to determine the source
- **Level-triggered**: Lines were held low until the interrupt was acknowledged,
  causing issues with shared interrupts
- **Routing complexity**: Physical traces required on the motherboard

MSI (Message Signaled Interrupts)
---------------------------------

MSI was introduced with PCI 2.2 to address these issues. Instead of dedicated
interrupt wires, devices generate **memory writes** to a specific address. The
interrupt controller (APIC on x86, GIC on ARM) monitors for these writes and
triggers the appropriate CPU interrupt.

Key characteristics of MSI:

- Up to 32 interrupt vectors per device
- All vectors share the same base address (only data varies)
- Simpler than MSI-X but less flexible

MSI-X (Message Signaled Interrupts - Extended)
----------------------------------------------

MSI-X, introduced with PCI 3.0, extends MSI with:

- Up to **2048 interrupt vectors** per device
- **Independent address and data** for each vector
- **Per-vector masking** capability
- Stored in a **BAR-mapped table** (not config space)

This makes MSI-X ideal for high-performance devices like NICs (one interrupt
per queue), NVMe controllers, and our BSA Exerciser (testing interrupt
functionality).

How MSI-X Works at the Hardware Level
-------------------------------------

When a device wants to interrupt the CPU:

1. Device reads the MSI-X table entry for the desired vector
2. Device checks if the vector is masked
3. If unmasked, device issues a **Memory Write TLP** (Transaction Layer Packet):

   - **Address** = Message Address from table entry
   - **Data** = Message Data from table entry

4. The TLP travels upstream through the PCIe hierarchy to the Root Complex
5. Root Complex delivers the write to the interrupt controller's address space
6. Interrupt controller triggers the corresponding CPU interrupt
7. CPU executes the device driver's interrupt handler

The message address typically points to the interrupt controller's registers:

- **x86 (APIC)**: ``0xFEE00000`` region
- **ARM (GIC)**: System-dependent, configured by firmware

The message data encodes which interrupt vector the controller should trigger.

PCIe Configuration for MSI-X
----------------------------

MSI-X capability is advertised in the PCIe configuration space:

.. code-block:: text

   MSI-X Capability Structure (ID = 0x11)
   ┌─────────────────────────────────────────────────────────────┐
   │ Offset 0x00: Capability ID (0x11) | Next Ptr | Message Ctrl │
   │ Offset 0x04: Table Offset/BIR                               │
   │ Offset 0x08: PBA Offset/BIR                                 │
   └─────────────────────────────────────────────────────────────┘

Key fields:

- **Message Control**: Table size (N-1 encoded), function mask, enable bit
- **Table Offset/BIR**: Which BAR contains the table and byte offset within it
- **PBA Offset/BIR**: Which BAR contains the Pending Bit Array

The host reads this capability to discover:

- How many vectors the device supports
- Which BARs to map for table and PBA access

BSA Exerciser MSI-X Implementation
==================================

BAR Layout
----------

The BSA Exerciser uses the following BAR configuration for MSI-X:

.. list-table:: BAR Allocation
   :header-rows: 1
   :widths: 15 20 65

   * - BAR
     - Size
     - Purpose
   * - BAR0
     - 4KB
     - Control/Status Registers (CSRs), including MSI-X trigger
   * - BAR1
     - 16KB
     - DMA Buffer (reserved for Phase 4)
   * - BAR2
     - 32KB
     - MSI-X Table (16 vectors x 16 bytes, 256 bytes used)
   * - BAR3
     - --
     - Disabled
   * - BAR4
     - --
     - Disabled
   * - BAR5
     - 4KB
     - MSI-X Pending Bit Array (16 bits used)

MSI-X Table Structure (BAR2)
----------------------------

Each table entry is 16 bytes (4 DWORDs). The exerciser implements 16 entries:

.. code-block:: text

   Offset  Size     Field              Description
   ──────────────────────────────────────────────────────────────
   0x00    32-bit   Message Addr Lo    Lower 32 bits of target address
   0x04    32-bit   Message Addr Hi    Upper 32 bits (64-bit addressing)
   0x08    32-bit   Message Data       Value written to trigger interrupt
   0x0C    32-bit   Vector Control     Bit 0: Mask (1=masked, 0=enabled)

Total implemented size: 16 vectors x 16 bytes = 256 bytes (within a 32KB BAR window)

**Memory organisation**: The table is implemented as dual-port memory:

- **Port A**: PCIe host access (read/write via BAR2)
- **Port B**: Internal read access for the MSI-X controller

All vectors are **masked by default** (Vector Control bit 0 = 1).

Pending Bit Array (BAR5)
------------------------

The PBA is a bitmap with one bit per vector:

- **Bit set (1)**: Interrupt is pending - the device attempted to signal this
  vector while it was masked
- **Bit clear (0)**: No pending interrupt

.. code-block:: text

   PBA Layout (16 vectors):
   ┌─────────────────────────────────────────────────────────────┐
   │ DWORD 0: Vectors 0-15 (bits 0-15), bits 16-31 reserved      │
   └─────────────────────────────────────────────────────────────┘

Total implemented size: 16 bits (lower bits of DWORD 0 within a 4KB BAR window)

The PBA is **read-only from the host's perspective**. Writes from the host are
silently ignored. The MSI-X controller sets/clears pending bits internally.

Host Driver Interaction
-----------------------

The host OS/driver programs the MSI-X table during device initialization:

**1. Enumeration**: The OS reads the MSI-X capability in config space to discover
the table size and BAR locations.

**2. Vector Allocation**: The OS requests interrupt vectors from the platform's
interrupt controller (APIC/GIC).

**3. Table Programming**: For each allocated vector, the driver writes:

.. code-block:: c

   // Pseudocode - Linux kernel driver
   void __iomem *table = pci_iomap(pdev, 2, 0);  // Map BAR2

   for (int i = 0; i < num_vectors; i++) {
       // Get address/data from interrupt controller
       struct msi_msg msg;
       get_cached_msi_msg(irq, &msg);

       // Program table entry
       void __iomem *entry = table + (i * 16);
       writel(msg.address_lo, entry + 0x00);
       writel(msg.address_hi, entry + 0x04);
       writel(msg.data,       entry + 0x08);
       writel(0x00000000,     entry + 0x0C);  // Unmask
   }

**4. Enable MSI-X**: The driver sets the MSI-X Enable bit in the Message Control
register.

From the BSA Exerciser's perspective, these writes arrive as Memory Write TLPs
on BAR2, which the ``LitePCIeMSIXTable`` module handles by updating its internal
memory.

Interrupt Generation Flow
-------------------------

When the BSA Exerciser needs to signal an interrupt:

.. code-block:: text

   1. Trigger Source
      └── Software: Write to MSICTL (BAR0)
                │
                ▼
   2. MSI-X Controller receives trigger for vector N
                │
                ▼
   3. Controller reads table entry N (via internal port)
      - Fetches: msg_addr, msg_data, mask bit
      - Takes 5 clock cycles (sequential DWORD reads)
                │
                ▼
   4. Check mask bit
      ├── MASKED (bit=1):
      │   └── Set pending bit in PBA, return to idle
      └── UNMASKED (bit=0):
          └── Continue to step 5
                │
                ▼
   5. Issue Memory Write TLP
      - Address = msg_addr from table
      - Data = msg_data from table
      - Length = 1 DWORD
                │
                ▼
   6. TLP sent upstream via PCIe link
                │
                ▼
   7. Root Complex delivers to interrupt controller
                │
                ▼
   8. CPU interrupt triggered, driver ISR executes

Software-Triggered Interrupts (BSA Testing)
-------------------------------------------

For BSA compliance testing, software triggers MSI-X vectors via ``MSICTL``:

.. list-table:: MSI-X Control (MSICTL, BAR0)
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Bits
     - Description
   * - vector_id
     - [10:0]
     - MSI-X vector number to trigger (0-15 used)
   * - trigger
     - [31]
     - Write 1 to trigger the specified vector (self-clearing)

Usage from host:

.. code-block:: c

   // Trigger vector 5
   void __iomem *csr = pci_iomap(pdev, 0, 0);  // Map BAR0

   // Trigger: vector=5, trigger bit=1
   writel((5) | (1u << 31), csr + MSICTL);

This allows BSA test suites to verify:

- Interrupt delivery to specific vectors
- Masking behaviour (pending bit set when masked)
- Address translation (SMMU/IOMMU) of interrupt writes
- Interrupt controller configuration
