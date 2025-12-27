PCIe Byte-Swap Architecture
============================

.. warning::
   This is one of the most confusing aspects of working with PCIe on Xilinx FPGAs.
   Read this document carefully before writing any TLP handling code.

The Problem: Xilinx's "Helpful" Byte Swap
-----------------------------------------

The Xilinx 7-Series (and UltraScale) PCIe IP cores perform an **implicit byte-swap**
within each 32-bit DWORD between the AXI-Stream interface and the PCIe wire.

This swap is:

* **Always on** - you cannot disable it
* **Per-DWORD** - bytes are reversed within each 32-bit word
* **Undocumented** in obvious places (buried in forums and app notes)

The mapping is::

    AXI Byte 0 ↔ TLP Byte 3
    AXI Byte 1 ↔ TLP Byte 2
    AXI Byte 2 ↔ TLP Byte 1
    AXI Byte 3 ↔ TLP Byte 0

Why Xilinx Does This
~~~~~~~~~~~~~~~~~~~~

The stated rationale is that PCIe TLP headers are defined in big-endian format
(byte 0 contains the high bits of DW0), but AXI-Stream buses are typically
little-endian. By swapping, Xilinx allows you to construct headers using
"natural" little-endian bit positions.

The drawback is that **payload data also gets swapped**, which is usually not
what you want for DMA transfers.

How LitePCIe Handles This
-------------------------

LitePCIe uses the ``endianness`` parameter to pre-compensate for Xilinx's swap:

* ``endianness="big"`` (used by S7PCIEPHY): LitePCIe swaps before sending to PHY
* ``endianness="little"`` (used by USPPCIEPHY): No pre-swap

With ``endianness="big"``, the data flow is::

    TX Path:
    ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
    │ LitePCIe        │     │ PHY Interface   │     │ Xilinx PCIe IP  │     │ PCIe Wire       │
    │ Internal        │────▶│ (AXI-Stream)    │────▶│                 │────▶│                 │
    │                 │swap │                 │     │                 │swap │                 │
    │ Little-Endian   │     │ Big-Endian      │     │ Little-Endian   │     │ Correct Format  │
    │ DW0: 0xDDCCBBAA │     │ DW0: 0xAABBCCDD │     │ DW0: 0xDDCCBBAA │     │ B0=AA B1=BB ... │
    └─────────────────┘     └─────────────────┘     └─────────────────┘     └─────────────────┘
                                    ▲
                                    │
                            TESTBENCH OPERATES HERE

The two swaps cancel out, resulting in correct wire format.

PHY Interface Format
--------------------

At the PHY interface (``phy.sink`` and ``phy.source``), data is in **big-endian
DWORD format**:

Headers
~~~~~~~

Header fields are positioned according to PCIe spec byte positions:

* ``fmt`` (format): bits [31:29] of DW0
* ``type``: bits [28:24] of DW0
* ``length``: bits [9:0] of DW0
* ``requester_id``: bits [31:16] of DW1
* ``tag``: bits [15:8] of DW1
* ``address``: bits [31:2] of DW2 (for 32-bit addressing)

These match the ``HeaderField`` definitions in ``litepcie/tlp/common.py``.

Data Payloads
~~~~~~~~~~~~~

Data is also big-endian: byte 0 of your data goes to bits [31:24] of the DWORD.

Testbench Guidelines
--------------------

When writing testbench code that operates at PHY level:

Building TLPs
~~~~~~~~~~~~~

Headers - use PCIe spec bit positions directly::

    # Correct - fmt at bits [31:29]
    dw0 = (0b010 << 29) | (0b00000 << 24) | (length & 0x3FF)
    dw1 = (requester_id << 16) | (tag << 8) | (last_be << 4) | first_be
    dw2 = address & 0xFFFFFFFC

Data - use big-endian byte order::

    # Correct - byte 0 at MSB
    data_dw = int.from_bytes(data_bytes[0:4], 'big')

Parsing TLPs
~~~~~~~~~~~~

Headers - use bit positions directly, **NO byte swap**::

    # Correct
    dw0 = beats[0]['dat'] & 0xFFFFFFFF
    fmt = (dw0 >> 29) & 0x7
    tlp_type = (dw0 >> 24) & 0x1F

    # WRONG - don't do this!
    dw0 = bswap32(beats[0]['dat'] & 0xFFFFFFFF)  # Breaks field extraction!

Data - swap if you need native integers::

    # Correct - swap data payload to get native little-endian value
    raw_data = (beats[1]['dat'] >> 32) & 0xFFFFFFFF
    native_value = int.from_bytes(raw_data.to_bytes(4, 'big'), 'little')

Summary Table
-------------

.. list-table:: Byte-Swap Rules at PHY Interface
   :header-rows: 1
   :widths: 30 35 35

   * - Operation
     - Swap Required?
     - Example
   * - Build TLP headers
     - No
     - ``(fmt << 29) | (type << 24) | ...``
   * - Build TLP data
     - No (use 'big')
     - ``int.from_bytes(data, 'big')``
   * - Parse TLP headers
     - **No**
     - ``fmt = (dw0 >> 29) & 0x7``
   * - Parse TLP data
     - Yes (to native)
     - ``int.from_bytes(raw.to_bytes(4,'big'),'little')``

Common Mistakes
---------------

1. **Swapping headers when parsing** - The header is already in a format where
   bit positions match the PCIe spec. Swapping moves fields to wrong positions.

2. **Inconsistent data handling** - Building data with ``'big'`` but parsing
   without swap, or vice versa.

3. **Confusing internal vs PHY format** - LitePCIe's internal format (after
   depacketizer, before packetizer) is little-endian. PHY format is big-endian.

References
----------

* Xilinx Forum: "AXI Stream byte ordering in PCI Express designs"
* Xilinx PG054: 7 Series FPGAs Integrated Block for PCI Express
* PCIe Base Specification: TLP Header format definitions
