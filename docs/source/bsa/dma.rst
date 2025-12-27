DMA Requirements
================

The BSA Exerciser must support controlled DMA transfers between host memory
and an internal buffer, with configurable TLP attributes.

Transfer Directions
-------------------

DMA Read (Host to Exerciser)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Software configures target host address, length, and buffer offset
2. Exerciser issues Memory Read TLP(s) to host
3. Completion data is stored in internal buffer
4. Status indicates success or error (timeout, completion error)

DMA Write (Exerciser to Host)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Software prepares data in internal buffer
2. Software configures target host address, length, and buffer offset
3. Exerciser issues Memory Write TLP(s) to host
4. No completion expected (posted write)

TLP Attributes
--------------

The exerciser must support configurable TLP attributes:

No-Snoop (NS)
~~~~~~~~~~~~~

When set, the No-Snoop bit in the TLP header indicates that the requester
does not require cache coherency. This is used to test IOMMU handling of
non-coherent DMA.

Address Type (AT)
~~~~~~~~~~~~~~~~~

The AT field indicates address translation status:

* ``00``: Untranslated (default)
* ``01``: Translation Request
* ``10``: Translated
* ``11``: Reserved

This allows testing of IOMMU behavior with pre-translated addresses.

PASID TLP Prefix
~~~~~~~~~~~~~~~~

When enabled, a PASID TLP prefix is prepended to DMA TLPs:

* **PASID Value**: 20-bit Process Address Space ID
* **Privileged Mode Requested (PMR)**: Privileged access flag
* **Execute Requested**: Instruction fetch flag

This enables testing of process-level isolation in the IOMMU.

Buffer Requirements
-------------------

The internal DMA buffer must:

* Be accessible from both the DMA engine and host (via BAR1)
* Support at least 16KB capacity
* Allow byte-granular host writes for test data preparation
* Support efficient bulk transfers to/from DMA engine

Error Handling
--------------

The exerciser must report:

* **Success**: Transfer completed without error
* **Completion Error**: Received completion with error status
* **Timeout**: No completion received within timeout period
