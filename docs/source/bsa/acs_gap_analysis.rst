ACS Requirements Gap Analysis
=============================

This page records the current alignment between the ACS exerciser requirements
(see ``external/sysarch-acs/docs/pcie/Exerciser.md`` and
``external/sysarch-acs/docs/pcie/Exerciser_API_porting_guide.md``) and the
implemented RTL in this repository.

Summary of alignment
--------------------

Implemented and aligned:

* BAR0 register map, DMA controls, PASID value, RID override, TXN_TRACE
  (``src/bsa_pcie_exerciser/gateware/core/bsa_registers.py``).
* DMA engine + BAR1 buffer (read/write, no-snoop, AT, PASID, RID override)
  (``src/bsa_pcie_exerciser/gateware/dma``).
* Transaction monitor with ACS TXN_TRACE format and per-beat capture
  (``src/bsa_pcie_exerciser/gateware/monitor/txn_monitor.py``).
* ATS engine + ATC + invalidation handling
  (``src/bsa_pcie_exerciser/gateware/ats``).
* MSI-X table/PBA and INTx generation (``src/bsa_pcie_exerciser/gateware/msix``,
  ``src/bsa_pcie_exerciser/gateware/soc/base.py``).

Known gaps or mismatches
------------------------

* **ACS enforcement**: ACS ECAP is advertised and writable, but no hardware
  enforcement of ACS redirect/e2e blocking is implemented beyond existing
  routing behavior.
* **Config-space scope**: Extended capabilities (ATS/PASID/ACS/DPC) and DVSEC
  error injection are provided via the user-defined extended config space
  responder. Core capabilities remain owned by the PCIe hard IP.
* **Error injection semantics**: DVSEC-driven error injection and poison mode
  hook into the PCIe core ``cfg_err_*`` interface and BAR read path; coverage
  is limited to the error classes exposed by the core. ACS e023/e024/e027
  exercise error codes that do not map to a dedicated ``cfg_err_*`` input on
  the 7-series hard IP, so the corresponding AER status bits cannot be set.

Plan to close gaps
------------------

1. Validate ACS exerciser tests e020/e023/e024/e027/e028/e029/e036 and update
   docs/testcases to reflect supported/unsupported behaviors.
2. Decide whether ACS control bits should drive functional policy (redirect,
   egress control) or remain “informational only”.
3. Extend DVSEC error injection coverage if additional error classes are needed
   by future ACS tests or platform-specific validation.
