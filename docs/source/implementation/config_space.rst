Config-Space Responder
======================

The exerciser exposes ACS-required extended capabilities via a user-defined
extended configuration space responder. This allows ATS/PASID/ACS/DPC ECAPs
and a DVSEC error-injection control block to be advertised on 7-series parts.

Source: ``src/bsa_pcie_exerciser/gateware/config/pcie_config.py``

Overview
--------

The PCIe hard IP forwards configuration accesses at or above
``EXT_PCI_CFG_Space_Addr`` (DWORD address). The exerciser uses that window to
serve an ECAP chain starting at DWORD ``0x6B`` (byte ``0x1AC``), with each
capability linked via the Next Pointer field.

Capabilities (ECAP chain)
-------------------------

The chain is ordered as:

1. **ATS ECAP** (control register)
2. **PASID ECAP** (capability/control)
3. **ACS ECAP** (control)
4. **DPC ECAP** (control + status)
5. **DVSEC** (vendor-specific error injection control)

Software must follow the Next Pointer, not assume fixed addresses.

ATS Control (ECAP)
------------------

The ATS ECAP control register bit ``[31]`` gates ATS usage:

* ``ATS_ENABLE`` = 1 enables ATS requests and ATC use.
* ``ATS_ENABLE`` = 0 blocks new ATS requests and forces ATC use off.

When ATS is disabled, the exerciser clears cached ATS results and ATC state.

PASID Capability (ECAP)
-----------------------

The PASID ECAP advertises the supported PASID width. The max PASID value
reported in bits ``[12:8]`` is fixed to 20 in this design.

ACS Control (ECAP)
------------------

ACS control bits are writable and visible to software. The exerciser does not
implement ACS enforcement policies beyond its existing routing behavior.

DPC Control/Status (ECAP)
-------------------------

The DPC control register enables DPC trigger reporting. When error injection
is used, the exerciser updates DPC status to indicate the reason code if
trigger bits are enabled. The status register is W1C.

DVSEC Error Injection
---------------------

The DVSEC provides a small control register for error injection and poison
mode. Bit definitions:

* ``[15:0]``  DVSEC ID (read-only, fixed to 0x0001)
* ``[16]``    Inject-on-DMA enable
* ``[17]``    Inject error immediately (self-clearing pulse)
* ``[18]``    Poison mode enable
* ``[30:20]`` Error code (mapped to ``cfg_err_*`` signals)
* ``[31]``    Fatal (sets DPC status reason)

Poison mode forces BAR0/BAR1 reads to return all 1s and drops writes; each
poisoned read also generates a poison error injection.

Error Code Mapping
------------------

Error codes drive the PCIe core ``cfg_err_*`` inputs. The current mapping
matches ``src/bsa_pcie_exerciser/gateware/soc/base.py``; common values include:

* ``0x0-0x7``: Correctable error (``cfg_err_cor``)
* ``0xA``: Poisoned (``cfg_err_poisoned``)
* ``0xC``: Completion timeout
* ``0xD``: Completion abort
* ``0xE``: Unexpected completion
* ``0x10``: Malformed TLP
* ``0x11``: ECRC error
* ``0x12``: Unsupported Request
* ``0x13``: ACS violation
* ``0x15``: MC blocked
* ``0x16``: Atomic egress blocked

Other codes map to internal correctable/uncorrectable errors as documented in
``base.py``.
