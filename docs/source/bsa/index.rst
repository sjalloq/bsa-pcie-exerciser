BSA Exerciser Requirements
==========================

This section documents the requirements from the ARM Base System Architecture
(BSA) specification for the PCIe Exerciser. These requirements define *what*
the exerciser must do, independent of implementation details.

The BSA PCIe Exerciser is a PCIe endpoint designed to aid validation of PCIe
subsystems, particularly SMMU/IOMMU functionality, cache coherency, address
translation, and interrupt handling.

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   overview
   dma
   interrupts
   ats
   registers
   testcases
