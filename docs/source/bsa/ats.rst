ATS Requirements
================

The BSA Exerciser must support PCIe Address Translation Services (ATS) to
enable testing of IOMMU translation and invalidation flows.

Address Translation Services
----------------------------

ATS allows PCIe endpoints to request address translations from the IOMMU
and cache them locally, reducing translation overhead for repeated accesses.

Translation Request
~~~~~~~~~~~~~~~~~~~

The exerciser issues ATS Translation Request TLPs:

* **Address**: Virtual/untranslated address to translate
* **No Write (NW)**: If set, only read permission requested
* **PASID prefix**: Optional process context for translation

The IOMMU responds with a Translation Completion containing:

* **Translated Address**: Physical address
* **Range Size**: Size of the translated region
* **Permissions**: Read/Write/Execute permissions
* **Success/Failure**: Translation status

Address Translation Cache (ATC)
-------------------------------

The exerciser maintains a local cache of translations:

Cache Entry Contents
~~~~~~~~~~~~~~~~~~~~

* Input (untranslated) address
* Output (translated) address
* Range size
* Permissions
* PASID (if applicable)

Cache Usage
~~~~~~~~~~~

When ``use_atc`` is enabled for DMA:

1. DMA engine looks up the target address in ATC
2. If hit: Uses translated address in TLP
3. If miss: Uses original address (software may need to request translation first)

Invalidation Handling
---------------------

The IOMMU can invalidate cached translations via ATS Invalidation Requests.

Invalidation Request Processing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When an invalidation request arrives:

1. **Check overlap**: Does invalidation range overlap with ATC entry?
2. **Check PASID**: Does PASID match (or is it global invalidation)?
3. **Coordinate with ATS engine**: If translation request in flight, signal retry
4. **Coordinate with DMA engine**: If DMA using cached translation, wait for completion
5. **Invalidate**: Clear the ATC entry
6. **Respond**: Send Invalidation Completion message

Invalidation Completion
~~~~~~~~~~~~~~~~~~~~~~~

The response is a PCIe Message TLP (not a Completion TLP):

* Format/Type: ``001 10010`` (4DW, Message routed by ID)
* Message Code: ``0x02`` (Invalidation Completion)
* Completion Code: Success/failure status

PASID Support
-------------

All ATS operations support PASID context:

* Translation requests can include PASID TLP prefix
* ATC entries are tagged with PASID
* Invalidations can target specific PASIDs or be global
* PASID matching is required for ATC lookup hits
