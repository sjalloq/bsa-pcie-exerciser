Implementation Guide
====================

This section documents the Migen/LiteX implementation of the BSA PCIe Exerciser.
It covers the hardware architecture, module design, and data flow through
the system.

The implementation is built on:

* **Migen**: Python-based HDL for generating Verilog
* **LiteX**: SoC builder framework
* **LitePCIe**: PCIe endpoint IP (with modifications for BAR hit and attributes)

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   architecture
   endpoint
   dma
   msix
   ats
   pasid
   monitor
