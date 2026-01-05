LitePCIe Modifications for BSA Exerciser
========================================

This document specifies the modifications required to LitePCIe to support the
ARM BSA/SBSA PCIe Exerciser requirements.

Overview
--------

The BSA ACS exerciser specification requires features not present in the
upstream LitePCIe implementation:

1. Multiple BARs (BAR0, BAR1, BAR2, BAR5)
2. BAR hit detection and routing
3. MSI-X support with 16 implemented vectors (32KB/4KB windows retained)
4. TLP attribute control (No-Snoop, Relaxed Ordering, etc.)

These modifications target the Xilinx 7-Series PHY (S7PCIEPHY) initially.

BAR Requirements
----------------

The BSA exerciser requires the following BAR configuration:

.. list-table:: BAR Configuration
   :header-rows: 1
   :widths: 10 15 15 60

   * - BAR
     - Size
     - 64-bit
     - Purpose
   * - BAR0
     - 4KB
     - No
     - Control/Status Registers
   * - BAR1
     - 16KB
     - No
     - DMA data buffer (``memory_bar`` default)
   * - BAR2
     - 32KB
     - No
     - MSI-X Table window (16 entries implemented)
   * - BAR3
     - --
     - --
     - Disabled
   * - BAR4
     - --
     - --
     - Disabled
   * - BAR5
     - 4KB
     - No
     - MSI-X PBA (Pending Bit Array)

BAR Usage
~~~~~~~~~

**BAR0 - Control/Status Registers**

Host software writes to BAR0 to configure and trigger exerciser operations.
Register map defined in ``docs/REGISTER_MAP.md``.

**BAR1 - DMA Data Buffer**

Local scratchpad memory on the FPGA for DMA operations:

- DMA Write (FPGA → Host): Host pre-loads data to BAR1, exerciser reads and
  sends to host memory
- DMA Read (Host → FPGA): Exerciser fetches from host memory, stores in BAR1
  for host to read back

The offset within BAR1 is configured via the DMA Offset Register (0x0C in BAR0).

**BAR2/BAR5 - MSI-X**

Standard MSI-X table and PBA locations as per PCIe specification.
Only the first 16 vectors are implemented; accesses beyond are reserved.

PHY Modifications
-----------------

S7PCIEPHY TCL Configuration
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The Xilinx 7-Series PCIe IP requires additional configuration parameters.
Current LitePCIe only configures BAR0:

.. code-block:: python

    # Current (litepcie/phy/s7pciephy.py:491-492)
    config = {
        "Bar0_Scale" : "Megabytes",
        "Bar0_Size"  : 1,
        ...
    }

Required additions for BSA:

.. code-block:: python

    config = {
        # BAR0 - CSRs
        "Bar0_Enabled"      : True,
        "Bar0_Type"         : "Memory",
        "Bar0_Scale"        : "Kilobytes",
        "Bar0_Size"         : 4,
        "Bar0_64bit"        : False,
        "Bar0_Prefetchable" : False,

        # BAR1 - DMA Buffer
        "Bar1_Enabled"      : True,
        "Bar1_Type"         : "Memory",
        "Bar1_Scale"        : "Kilobytes",
        "Bar1_Size"         : 16,
        "Bar1_64bit"        : False,
        "Bar1_Prefetchable" : False,

        # BAR2 - MSI-X Table
        "Bar2_Enabled"      : True,
        "Bar2_Type"         : "Memory",
        "Bar2_Scale"        : "Kilobytes",
        "Bar2_Size"         : 32,
        "Bar2_64bit"        : False,
        "Bar2_Prefetchable" : False,

        # BAR3/BAR4 disabled (default)

        # BAR5 - MSI-X PBA
        "Bar5_Enabled"      : True,
        "Bar5_Type"         : "Memory",
        "Bar5_Scale"        : "Kilobytes",
        "Bar5_Size"         : 4,
        "Bar5_64bit"        : False,
        "Bar5_Prefetchable" : False,

        # MSI-X Configuration
        "MSIx_Enabled"      : True,
        "MSIx_Table_Size"   : "7FF",  # 2048 advertised (N-1 encoding)
        "MSIx_Table_Bar"    : 2,
        "MSIx_PBA_Bar"      : 5,
        "MSIx_Table_Offset" : "0",
        "MSIx_PBA_Offset"   : "0",
        ...
    }

BAR Hit Sideband Signals
~~~~~~~~~~~~~~~~~~~~~~~~

The Xilinx 7-Series PCIe IP provides BAR hit information in the AXI-Stream
``tuser`` sideband signal on the receive path:

.. list-table:: m_axis_rx_tuser BAR Hit Bits (7-Series)
   :header-rows: 1
   :widths: 20 80

   * - Bit
     - Description
   * - tuser[2]
     - BAR0 hit
   * - tuser[3]
     - BAR1 hit
   * - tuser[4]
     - BAR2 hit
   * - tuser[5]
     - BAR3 hit
   * - tuser[6]
     - BAR4 hit
   * - tuser[7]
     - BAR5 hit
   * - tuser[8]
     - BAR6 hit (Expansion ROM)

**Current State**: LitePCIe S7PCIEPHY does NOT extract BAR hit. It only uses
SOF/EOF bits:

.. code-block:: python

    # s7pciephy.py:432-433
    rx_is_sof = m_axis_rx_tuser[10:15]
    rx_is_eof = m_axis_rx_tuser[17:22]

**Required Addition**:

.. code-block:: python

    # Add to S7PCIEPHY
    self.bar_hit = Signal(7)
    self.comb += self.bar_hit.eq(m_axis_rx_tuser[2:9])

This signal must then be propagated through the PHY layout to the depacketizer
for routing incoming TLPs to the appropriate handler.

PHY Parameters
~~~~~~~~~~~~~~

New parameters required for the BSA PHY:

.. code-block:: python

    class S7PCIEPHY_BSA(S7PCIEPHY):
        def __init__(self, platform, pads,
            data_width  = 64,
            bar0_size   = 0x1000,   # 4KB
            bar1_size   = 0x4000,   # 16KB
            bar2_size   = 0x8000,   # 32KB
            bar5_size   = 0x1000,   # 4KB
            ...
        ):
            self.bar0_size = bar0_size
            self.bar0_mask = get_bar_mask(bar0_size)
            self.bar1_size = bar1_size
            self.bar1_mask = get_bar_mask(bar1_size)
            self.bar2_size = bar2_size
            self.bar2_mask = get_bar_mask(bar2_size)
            self.bar5_size = bar5_size
            self.bar5_mask = get_bar_mask(bar5_size)
            ...

Endpoint Modifications
----------------------

The endpoint needs to route incoming requests based on BAR hit:

.. code-block:: text

    ┌─────────────────────────────────────────────────────────────┐
    │                    LitePCIeEndpoint                         │
    │                                                             │
    │  ┌─────────────┐         ┌───────────────┐                 │
    │  │ Depacketizer│────────►│  BAR Router   │                 │
    │  │  (extract   │         │               │                 │
    │  │   bar_hit)  │         │ bar_hit[0] ──►│──► BAR0 (CSRs)  │
    │  └─────────────┘         │ bar_hit[1] ──►│──► BAR1 (DMA)   │
    │                          │ bar_hit[2] ──►│──► BAR2 (MSI-X) │
    │                          │ bar_hit[5] ──►│──► BAR5 (PBA)   │
    │                          └───────────────┘                 │
    └─────────────────────────────────────────────────────────────┘

DMA Configuration
-----------------

MPS/MRRS Handling
~~~~~~~~~~~~~~~~~

Maximum Payload Size (MPS) and Maximum Read Request Size (MRRS) are handled
dynamically in LitePCIe:

1. PHY reads ``dcommand`` register from PCIe config space (Device Control)
2. ``convert_size()`` decodes the 3-bit fields to byte values
3. DMA splitter uses ``endpoint.phy.max_request_size`` and
   ``endpoint.phy.max_payload_size``

.. code-block:: python

    # s7pciephy.py:138-144
    def convert_size(command, size, max_size):
        cases = {}
        value = 128
        for i in range(6):
            cases[i] = size.eq(value)
            value = min(value*2, max_size)
        return Case(command, cases)

Maximum capability is capped at 512 bytes in the current implementation.

TLP Stream Interface
~~~~~~~~~~~~~~~~~~~~

The ``first`` and ``last`` signals bound TLP data payloads:

.. list-table:: first/last Signal Behavior
   :header-rows: 1
   :widths: 20 40 40

   * - Transaction
     - first
     - last
   * - Read Request
     - Always 1
     - Always 1 (header only, no data)
   * - Write Request
     - 1 on first data beat
     - 1 on final data beat

For a 256-byte write at 64-bit data width (32 beats):

- Beat 0: ``first=1``, ``last=0``
- Beats 1-30: ``first=0``, ``last=0``
- Beat 31: ``first=0``, ``last=1``

Implementation Approach
-----------------------

Recommended approach is to create a new PHY class inheriting from S7PCIEPHY:

1. **Create S7PCIEPHY_BSA** in ``litepcie/phy/s7pciephy_bsa.py``

   - Override ``add_sources()`` with BSA BAR configuration
   - Add BAR hit extraction from tuser
   - Add bar1/bar2/bar5 size/mask attributes

2. **Extend PHY layout** to include ``bar_hit`` signal

3. **Modify depacketizer** to extract and propagate BAR hit

4. **Create BAR router** module to demux requests by BAR

5. **Extend endpoint** to expose multiple slave ports (one per BAR)

This can be maintained in the ``sjalloq/litepcie`` fork on the
``feature/tlp-attributes`` branch.

References
----------

- `PG054 - 7 Series FPGAs Integrated Block for PCI Express
  <https://www.xilinx.com/support/documents/ip_documentation/pcie_7x/v3_3/pg054-7series-pcie.pdf>`_
- `BSA ACS Exerciser Specification
  <https://github.com/ARM-software/bsa-acs/blob/main/docs/PCIe_Exerciser/Exerciser.md>`_
- `PCIe Configurable Hierarchy
  <https://github.com/ARM-software/bsa-acs/blob/main/docs/pcie/PCIeConfigurableHierarchy.md>`_
