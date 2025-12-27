Overview
========

The ARM BSA PCIe Exerciser is a specialized PCIe endpoint defined in the
ARM Base System Architecture (BSA) specification. Its purpose is to enable
compliance testing of PCIe subsystems on ARM platforms.

Purpose
-------

The exerciser allows test software to:

* Perform controlled DMA transfers with specific TLP attributes
* Test SMMU/IOMMU address translation and protection
* Validate interrupt delivery (MSI-X and legacy INTx)
* Exercise Address Translation Services (ATS) and PASID functionality
* Monitor incoming PCIe transactions

Use Cases
---------

SMMU/IOMMU Testing
~~~~~~~~~~~~~~~~~~

The exerciser can issue DMA requests with configurable attributes:

* **No-Snoop bit**: Tests cache coherency handling
* **Address Type (AT) field**: Tests translated vs untranslated address handling
* **PASID TLP prefix**: Tests process-level isolation

By controlling these attributes, test software can verify that the IOMMU
correctly enforces access control policies.

ATS Testing
~~~~~~~~~~~

The exerciser supports PCIe Address Translation Services:

* **Translation Requests**: Request address translations from the IOMMU
* **Address Translation Cache (ATC)**: Cache translations for reuse
* **Invalidation Handling**: Respond to IOMMU invalidation requests

This enables testing of ATS-aware IOMMU implementations.

Interrupt Testing
~~~~~~~~~~~~~~~~~

The exerciser supports multiple interrupt mechanisms:

* **MSI-X**: Up to 2048 vectors with full table/PBA implementation
* **Legacy INTx**: Level-triggered interrupt assertion/deassertion

Test software can trigger arbitrary interrupt vectors and verify delivery.

Reference
---------

* `ARM BSA Specification <https://developer.arm.com/documentation/den0094/>`_
* `BSA ACS PCIe Exerciser <https://github.com/ARM-software/bsa-acs/blob/main/docs/PCIe_Exerciser.md>`_
