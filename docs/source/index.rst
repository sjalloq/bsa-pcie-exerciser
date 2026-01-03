BSA PCIe Exerciser Documentation
=================================

The BSA PCIe Exerciser is an FPGA-based PCIe endpoint for ARM Base System
Architecture (BSA) compliance testing. It enables validation of SMMU/IOMMU,
cache coherency, address translation, and interrupt handling.

Built on the LiteX/LitePCIe framework using Migen for hardware description.

Getting Started
---------------

* :doc:`bsa/overview` - What the exerciser does and why
* :doc:`implementation/architecture` - Top-level system design

.. toctree::
   :maxdepth: 2
   :caption: BSA Requirements:

   bsa/index

.. toctree::
   :maxdepth: 2
   :caption: Implementation:

   implementation/index

.. toctree::
   :maxdepth: 2
   :caption: Hardware:

   platforms/index

.. toctree::
   :maxdepth: 2
   :caption: USB Interface:

   etherbone/index

.. toctree::
   :maxdepth: 2
   :caption: Background:

   pcie/index
   litepcie/index
