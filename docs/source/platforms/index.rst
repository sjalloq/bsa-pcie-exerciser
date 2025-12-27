Target Platforms
================

The BSA PCIe Exerciser targets low-cost Artix-7 based PCIe cards with USB 3.0
interfaces for host communication and debug.

Supported Boards
----------------

.. list-table::
   :header-rows: 1
   :widths: 30 20 20 30

   * - Board
     - FPGA
     - PCIe
     - Status
   * - :doc:`squirrel`
     - XC7A35T-FGG484
     - Gen2 x1
     - Primary target
   * - SPEC-A7
     - XC7A50T-FGG484
     - Gen2 x1
     - Initial development

Board Families
--------------

Our investigation found two distinct board families sharing the same FPGA:

**LambdaConcept Family**
   Original PCIe Screamer boards. Has existing LiteX support in the
   ``pcie_screamer`` repository but uses different I/O pinout.

**Squirrel/Captain Family**
   Includes PCIeSquirrel and CaptainDMA boards. These are pin-compatible
   (identical XDC constraints). No existing LiteX platform - requires new
   platform file.

Despite using the same XC7A35T-FGG484 FPGA, the two families have completely
different PCB routing for all I/O except the PCIe GTP lanes.

.. toctree::
   :maxdepth: 2

   squirrel
   ft601
