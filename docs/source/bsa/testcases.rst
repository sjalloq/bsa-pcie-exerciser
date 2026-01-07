ACS Exerciser Testcases
=======================

This page summarizes the PCIe Exerciser testcases in the ACS tree at
``external/sysarch-acs/test_pool/exerciser``. Each row lists the test intent
and the exerciser features that must be implemented for the test to run.

Feature legend
--------------

- ``DMA``: DMA engine (DMACTL/DMA_LEN/DMA_BUS_ADDR/DMA_OFFSET, BAR1 buffer).
- ``TXN_MON``: Transaction monitor (TXN_CTRL/TXN_TRACE).
- ``MSI``: MSI/MSI-X generation (MSICTL) and MSI capability in config space.
- ``INTx``: Legacy interrupt generation (INTXCTL).
- ``ATS``: ATS request/response (ATSCTL + ATS result registers).
- ``PASID``: PASID enable/value for DMA/ATS (DMACTL[6], PASID_VAL).
- ``RID``: Requester ID override (RID_CTL).
- ``ADDR_TYPE``: DMA address type field (DMACTL[11:10]).
- ``NO_SNOOP``: DMA no-snoop attribute (DMACTL[5]).
- ``ERR_INJ``: Error injection interface used by AER/DPC tests.
- ``POISON``: Poisoned data generation for RAS tests.
- ``BAR_MMIO``: BAR memory window accessible by host software.
- ``CFG``: Config space fields/capabilities used by ACS tests.

Testcase matrix
---------------

.. list-table::
   :header-rows: 1
   :widths: 10 55 35

   * - Testcase
     - What is tested
     - Exerciser features required
   * - e001
     - Check P2P ACS functionality (ACS request routing, invalid RID, reserved AT)
     - ``DMA``, ``RID``, ``ADDR_TYPE``, ``BAR_MMIO``, ``CFG``
   * - e002
     - Check ACS Redirect Req Valid for P2P transactions
     - ``DMA``, ``BAR_MMIO``, ``CFG``
   * - e003
     - Arrival order and gathering checks using transaction monitor
     - ``TXN_MON``, ``BAR_MMIO``
   * - e004
     - MSI-capable device can be mapped and used to target ITS (GITS_TRANSLATER write)
     - ``MSI``, ``CFG``
   * - e006
     - Generate PCIe legacy interrupt (INTx)
     - ``INTx``, ``CFG``
   * - e007
     - PCIe I/O coherency using DMA reads/writes (no-snoop disabled)
     - ``DMA``, ``NO_SNOOP``, ``BAR_MMIO``
   * - e008 (test 8)
     - Tx pending bit clear correctness for RCiEP
     - ``DMA``, ``NO_SNOOP``, ``BAR_MMIO``, ``CFG``
   * - e008 (test 38)
     - Tx pending bit clear correctness for iEP
     - ``DMA``, ``NO_SNOOP``, ``BAR_MMIO``, ``CFG``
   * - e010
     - RP secondary-bus transactions are Type 0 (transaction monitor)
     - ``TXN_MON``, ``CFG``
   * - e011
     - MSI-capable device linked to ITS group
     - ``MSI``, ``CFG``
   * - e012
     - MSI to ITS block outside assigned group
     - ``MSI``, ``CFG``
   * - e013
     - MSI originating from different master
     - ``MSI``, ``CFG``
   * - e014
     - P2P transactions must not deadlock
     - ``DMA``, ``BAR_MMIO``, ``CFG``
   * - e015
     - ARI forwarding enable rule (transaction monitor)
     - ``TXN_MON``, ``CFG``
   * - e016
     - PCIe device memory access check
     - ``BAR_MMIO``, ``CFG``
   * - e017 (test 17)
     - BME functionality of RP using DMA
     - ``DMA``, ``BAR_MMIO``, ``CFG``
   * - e017 (test 34)
     - BME functionality of iEP RP using DMA
     - ``DMA``, ``BAR_MMIO``, ``CFG``
   * - e019
     - PCIe address translation check (SMMU-backed DMA)
     - ``DMA``, ``BAR_MMIO``
   * - e020
     - ATS functionality check (ATS request + DMA using translated address)
     - ``ATS``, ``DMA``, ``PASID``, ``BAR_MMIO``
   * - e021
     - Arrival order and gathering check (transaction monitor)
     - ``TXN_MON``, ``BAR_MMIO``
   * - e022
     - PE 2/4/8-byte writes to PCIe as 2/4/8-byte (transaction monitor)
     - ``TXN_MON``, ``BAR_MMIO``
   * - e023
     - AER functionality for RPs (error injection)
     - ``ERR_INJ``, ``CFG``
       (Note: 7-series PCIe core exposes only a subset of AER error inputs;
       some error codes cannot assert the specific AER status bit expected by ACS.)
   * - e024
     - DPC functionality for RPs (error injection)
     - ``ERR_INJ``, ``CFG``
       (Note: config-read containment checks rely on root-port behavior; the
       exerciser cannot force UR responses for core-owned config space.)
   * - e025
     - 2/4/8-byte targeted writes (DMA + transaction monitor)
     - ``DMA``, ``TXN_MON``, ``BAR_MMIO``
   * - e026 (test 26)
     - Inbound writes seen in order
     - ``DMA``, ``BAR_MMIO``
   * - e026 (test 32)
     - Ordered writes flush previous writes
     - ``DMA``, ``BAR_MMIO``
   * - e027
     - DPC trigger when RP-PIO unimplemented (error injection)
     - ``ERR_INJ``, ``CFG``
       (Note: config-read containment checks rely on root-port behavior; the
       exerciser cannot force UR responses for core-owned config space.)
   * - e028
     - RAS error record for poisoned data
     - ``POISON``, ``BAR_MMIO``
       (Note: poison mode forces BAR reads to all 1s; RAS error logging depends
       on platform support.)
   * - e029
     - RAS error record for external abort
     - ``BAR_MMIO``
       (Note: external abort behavior is platform-dependent; exerciser does not
       synthesize aborts beyond BAR decode/control.)
   * - e030
     - Enable/disable STE.DCP bit (SMMU translation behavior with DMA)
     - ``DMA``, ``BAR_MMIO``
   * - e033
     - MSI(-X) triggers interrupt with unique ID
     - ``MSI``, ``CFG``
   * - e035
     - MSI-capable device can target any ITS block
     - ``MSI``, ``CFG``
   * - e036
     - Generate PASID transactions (PASID-tagged DMA)
     - ``DMA``, ``PASID``, ``BAR_MMIO``
   * - e039
     - PCIe normal memory access check
     - ``BAR_MMIO``, ``CFG``

Reader notes
------------

- Many tests skip when platform prerequisites are missing (ITS, SMMU, ACS, AER, DPC, etc.).
- ACS uses the PAL to program 64-bit DMA/ATS bus addresses by writing low/high DWORDs at
  ``DMA_BUS_ADDR``/``ATS_ADDR`` offsets (see ``external/sysarch-acs/pal/*/pal_exerciser.c``).
- For CSR definitions and bitfields, see ``docs/source/bsa/registers.rst`` and
  ``external/sysarch-acs/docs/pcie/Exerciser.md``.
- Error injection and poison-mode controls are exposed via the DVSEC in the
  user extended config space (see ``docs/source/implementation/config_space.rst``).

Implementation limitations
--------------------------

- The 7-series PCIe hard IP exposes a limited set of ``cfg_err_*`` inputs.
  Some ACS error codes do not map to a dedicated input, so the corresponding
  AER status bit may not be set on the endpoint.
 - DPC containment behavior (config reads returning ``PCIE_UNKNOWN_RESPONSE``)
  is driven by upstream root-port behavior; the exerciser cannot override
  core-owned config responses after error injection.
