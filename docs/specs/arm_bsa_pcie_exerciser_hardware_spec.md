# ARM BSA PCIe Exerciser Hardware Specification

**Version:** 1.1
**Date:** December 2024 (Updated December 2025)
**Source:** Derived from ARM sysarch-acs repository (external/sysarch-acs/docs/pcie/Exerciser.md)

---

## 1. Overview

This document specifies the hardware requirements for a PCIe endpoint device that functions as an ARM Base System Architecture (BSA) compliance test exerciser. The exerciser is a programmable PCIe endpoint that can:

- Generate DMA read/write transactions with configurable TLP attributes
- Monitor and record inbound transactions from the host
- Generate MSI/MSI-X and legacy interrupts
- Support Address Translation Services (ATS)
- Support Process Address Space ID (PASID) transactions
- Inject PCIe errors for RAS testing

This specification is implementation-agnostic and can be realized in any HDL or FPGA platform.

---

## 2. PCIe Device Identification

### 2.1 Required Vendor/Device ID

| Field | Value | Notes |
|-------|-------|-------|
| Vendor ID | 0x13B5 | ARM Ltd. |
| Device ID | 0xED01 | Exerciser |
| Combined | 0xED0113B5 | Read as single 32-bit DWORD at config offset 0x00 |

The test infrastructure uses this ID to identify exerciser devices during enumeration.

### 2.2 Device Type

- **Header Type:** Type 0 (Endpoint)
- **Device/Port Type:** Root Complex Integrated Endpoint (RCiEP) or integrated Endpoint (iEP)

---

## 3. Base Address Registers (BARs)

### 3.1 Required BARs

| BAR | Size | Type | Purpose |
|-----|------|------|---------|
| BAR0 | ≥4KB | 64-bit Memory, Non-Prefetchable | Control/Status Registers |
| BAR2 | Optional | 64-bit Memory | MSI-X Table (if MSI-X used) |
| BAR4 | Optional | 64-bit Memory | MSI-X PBA (if MSI-X used) |

**Note:** BAR0 is mandatory. MSI-X BARs are required if MSI-X capability is implemented.

### 3.2 BAR0 Memory Space

BAR0 must be mapped as device memory (non-cacheable, non-prefetchable). The exerciser must properly decode accesses of 1, 2, 4, and 8 bytes to this region.

---

## 4. BAR0 Register Map

All registers are 32-bit unless otherwise specified. Multi-byte registers use little-endian byte ordering.

### 4.1 Register Summary

| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| 0x000 | MSICTL | R/W | MSI/MSI-X Control |
| 0x004 | INTXCTL | R/W | Legacy Interrupt Control |
| 0x008 | DMACTL | R/W | DMA Control Register |
| 0x00C | DMA_OFFSET | R/W | DMA Buffer Offset (within BAR1) |
| 0x010 | DMA_BUS_ADDR_LO | R/W | DMA Bus Address [31:0] |
| 0x014 | DMA_BUS_ADDR_HI | R/W | DMA Bus Address [63:32] |
| 0x018 | DMA_LEN | R/W | DMA Transfer Length |
| 0x01C | DMASTATUS | R/W | DMA Status |
| 0x020 | PASID_VAL | R/W | PASID Value for Transactions |
| 0x024 | ATSCTL | R/W | ATS Control and Status |
| 0x028 | ATS_ADDR_LO | RO | ATS Translated Address [31:0] |
| 0x02C | ATS_ADDR_HI | RO | ATS Translated Address [63:32] |
| 0x030 | ATS_RANGE_SIZE | RO | ATS Translated Range Size (bytes) |
| 0x038 | ATS_PERM | RO | ATS Reply Permissions |
| 0x03C | RID_CTL | R/W | Requester ID Override Control |
| 0x040 | TXN_TRACE | RO | Transaction Trace FIFO Read |
| 0x044 | TXN_CTRL | R/W | Transaction Monitor Control |
| 0x048-0x0FF | Reserved | - | Reserved for future use |

### 4.2 Detailed Register Definitions

---

#### 4.2.1 MSICTL (Offset 0x000)

MSI/MSI-X interrupt generation control.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 10:0 | VECTOR_ID | R/W | 0 | MSI-X vector index to trigger (0-2047) |
| 30:11 | Reserved | RO | 0 | Reserved |
| 31 | TRIGGER | R/W | 0 | Write 1 to generate MSI. Self-clearing. |

**Behavior:**
- Writing 1 to TRIGGER with a valid VECTOR_ID causes the exerciser to issue an MSI-X write transaction to the address/data configured in the MSI-X table for that vector.
- TRIGGER bit clears automatically after the MSI is sent.
- If the vector is masked (in MSI-X table), the corresponding PBA bit is set instead.

---

#### 4.2.2 INTXCTL (Offset 0x004)

Legacy interrupt (INTx) control.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 0 | ASSERT | R/W | 0 | 1 = Assert INTx, 0 = Deassert INTx |
| 31:1 | Reserved | RO | 0 | Reserved |

**Behavior:**
- Writing 1 to ASSERT causes the exerciser to assert its legacy interrupt line.
- Writing 0 to ASSERT (or writing with bit 0 = 0) deasserts the interrupt.
- The interrupt remains asserted until software explicitly clears it.

---

#### 4.2.3 DMACTL (Offset 0x008)

Primary DMA control register.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 3:0 | TRIGGER | R/W | 0 | Write 0x1 to start DMA. Self-clearing. Values 0x2-0xF reserved. |
| 4 | DIRECTION | R/W | 0 | 0 = Read from host (host→exerciser), 1 = Write to host (exerciser→host) |
| 5 | NO_SNOOP | R/W | 0 | 0 = Snoop, 1 = No-Snoop attribute in TLP |
| 6 | PASID_EN | R/W | 0 | 1 = Include PASID TLP Prefix |
| 7 | PRIVILEGED | R/W | 0 | 1 = Privileged access mode (requires PASID_EN=1) |
| 8 | INSTRUCTION | R/W | 0 | 1 = Instruction access (requires PASID_EN=1) |
| 9 | USE_ATC | R/W | 0 | 1 = Use ATC for input address translation |
| 11:10 | ADDR_TYPE | R/W | 0 | Address Type: 0=Default/Untranslated, 1=Untranslated, 2=Translated, 3=Reserved |
| 31:12 | Reserved | RO | 0 | Reserved |

**Behavior:**
- Before writing TRIGGER=1, software must configure DMA_BUS_ADDR, DMA_LEN, and DMA_OFFSET.
- The TRIGGER field self-clears when DMA completes.
- DIRECTION determines whether the exerciser reads from host memory or writes to host memory.
- When PASID_EN=1, transactions include PASID TLP prefix with value from PASID_VAL register.
- When USE_ATC=1 and the DMA address is in the ATC input address range, the address is translated using the cached ATS result.
- Setting ADDR_TYPE=2 (Translated) with USE_ATC=1 is an error and will be rejected.

---

#### 4.2.4 DMA_OFFSET (Offset 0x00C)

DMA buffer offset within BAR1 region.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 31:0 | OFFSET | R/W | 0 | Byte offset from base of BAR1 for DMA data storage |

**Notes:**
- For DMA reads from host: data is stored at BAR1 + OFFSET
- For DMA writes to host: data is sourced from BAR1 + OFFSET
- OFFSET + DMA_LEN must not exceed BAR1 size

---

#### 4.2.5 DMA_BUS_ADDR (Offset 0x010-0x014)

64-bit target address for DMA operations.

| Register | Bits | Description |
|----------|------|-------------|
| DMA_BUS_ADDR_LO (0x010) | 31:0 | Address bits [31:0] |
| DMA_BUS_ADDR_HI (0x014) | 31:0 | Address bits [63:32] |

**Notes:**
- Address must be naturally aligned to the transaction size.
- For ATS transactions, this is the untranslated (virtual) address.

---

#### 4.2.6 DMA_LEN (Offset 0x018)

DMA transfer length in bytes.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 31:0 | LENGTH | R/W | 0 | Transfer length in bytes |

**Notes:**
- Maximum supported length is implementation-defined but must be at least 4KB.
- The exerciser may break large transfers into multiple TLPs as required by Max Payload Size.

---

#### 4.2.7 DMASTATUS (Offset 0x01C)

DMA status register.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 1:0 | STATUS | RO | 0 | Error codes: 0=No error, 1=Range out of bounds, 2=Internal error, 3=Reserved |
| 2 | CLEAR | WO | 0 | Write 1 to clear DMA status |
| 31:3 | Reserved | RO | 0 | Reserved |

**Behavior:**
- STATUS reflects the result of the last DMA operation.
- Write 1 to CLEAR to reset the status register before starting a new DMA.

---

#### 4.2.8 PASID_VAL (Offset 0x020)

PASID value for DMA transactions.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 19:0 | PASID | R/W | 0 | 20-bit PASID value |
| 31:20 | Reserved | RO | 0 | Reserved |

**Notes:**
- Only used when DMACTL.PASID_EN = 1.
- The PASID width is determined by the PASID Extended Capability in PCIe config space.

---

#### 4.2.9 ATSCTL (Offset 0x024)

Address Translation Service control and status.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 0 | TRIGGER | WO | 0 | Write 1 to send ATS Translation Request. Self-clearing. |
| 1 | PRIVILEGED | R/W | 0 | 1 = Privileged access (requires PASID_EN=1) |
| 2 | NO_WRITE | R/W | 0 | 0 = Request R/W permission, 1 = Request read-only permission |
| 3 | PASID_EN | R/W | 0 | 1 = Include PASID in ATS request |
| 4 | EXEC_REQ | R/W | 0 | 1 = Request execute permission (requires PASID_EN=1) |
| 5 | CLEAR_ATC | W1C | 0 | Write 1 to clear ATC and last ATS results |
| 6 | IN_FLIGHT | RO | 0 | 1 = ATS request is in progress |
| 7 | SUCCESS | RO | 0 | 1 = Translation successful |
| 8 | CACHEABLE | RO | 0 | 1 = Translation result is cacheable (R/W != 0) |
| 9 | INVALIDATED | RO | 0 | 1 = ATC was invalidated |
| 31:10 | Reserved | RO | 0 | Reserved |

**Behavior:**
- Software writes the untranslated address to DMA_BUS_ADDR, then writes TRIGGER=1.
- The exerciser sends an ATS Translation Request TLP.
- While in flight, IN_FLIGHT=1. When complete, SUCCESS indicates result.
- If translation succeeded, translated address is in ATS_ADDR, size in ATS_RANGE_SIZE, permissions in ATS_PERM.
- Writing CLEAR_ATC=1 invalidates the ATC and clears all ATS result registers.

---

#### 4.2.10 ATS_ADDR (Offset 0x028-0x02C)

Translated address from ATS (read-only).

| Register | Bits | Description |
|----------|------|-------------|
| ATS_ADDR_LO (0x028) | 31:0 | Translated address bits [31:0] |
| ATS_ADDR_HI (0x02C) | 31:0 | Translated address bits [63:32] |

---

#### 4.2.11 ATS_RANGE_SIZE (Offset 0x030)

Size of the ATS translated address region (read-only).

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 31:0 | SIZE | RO | 0 | Size of translated region in bytes |

**Notes:**
- Set by hardware after successful ATS translation.
- DMA transactions using ATC must fit entirely within this range.

---

#### 4.2.12 ATS_PERM (Offset 0x038)

ATS reply permissions (read-only).

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 0 | EXEC | RO | 0 | 1 = Execute permission granted |
| 1 | WRITE | RO | 0 | 1 = Write permission granted |
| 2 | READ | RO | 0 | 1 = Read permission granted |
| 3 | EXEC_PRIV | RO | 0 | 1 = Execute permission granted for privileged mode |
| 4 | WRITE_PRIV | RO | 0 | 1 = Write permission granted for privileged mode |
| 5 | Reserved | RO | 0 | Reserved |
| 6 | READ_PRIV | RO | 0 | 1 = Read permission granted for privileged mode |
| 31:7 | Reserved | RO | 0 | Reserved |

---

#### 4.2.13 RID_CTL (Offset 0x03C)

Requester ID override control.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 15:0 | REQ_ID | R/W | 0 | Requester ID value (Bus[15:8], Dev[7:3], Func[2:0]) |
| 30:16 | Reserved | RO | 0 | Reserved |
| 31 | VALID | R/W | 0 | 1 = Use REQ_ID for transactions, 0 = Use actual BDF |

**Behavior:**
- When VALID=1, all outbound transactions use REQ_ID instead of the exerciser's actual BDF.
- This is used for testing ACS and requester ID validation.

---

#### 4.2.14 TXN_TRACE (Offset 0x040)

Transaction trace FIFO read port (read-only).

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 31:0 | DATA | RO | 0xFFFFFFFF | Next word from transaction trace FIFO |

**FIFO Record Format:**

Each captured transaction consists of 5 consecutive 32-bit reads:

| Read # | Content |
|--------|---------|
| 1 | TX_ATTRIBUTES (see below) |
| 2 | ADDRESS[31:0] |
| 3 | ADDRESS[63:32] |
| 4 | DATA[31:0] |
| 5 | DATA[63:32] |

**TX_ATTRIBUTES Format:**

Per ARM Exerciser spec, the lower 16 bits indicate transaction type/attributes, upper 16 bits indicate size:

| Bits | Field | Description |
|------|-------|-------------|
| 0 | TYPE | Request type (defaults to 0) |
| 1 | RW | 1 = Read, 0 = Write |
| 2 | CFG_MEM | 1 = Config transaction, 0 = Memory transaction |
| 15:3 | Reserved | Reserved |
| 31:16 | SIZE | Transaction byte size (bit N set means 2^N bytes) |

**Note:** The SIZE field uses a one-hot encoding where bit position indicates log2(bytes). For example, if size is 8 bytes, bit 3 (value 0x0008 in upper 16 bits) is set, resulting in TX_ATTRIBUTES[31:16] = 0x0008.

**Behavior:**
- Reading TXN_TRACE when FIFO is empty returns 0xFFFFFFFF.
- Each read advances to the next word; 5 reads extract one complete record.
- Software should read until 0xFFFFFFFF to drain the FIFO.

---

#### 4.2.15 TXN_CTRL (Offset 0x044)

Transaction monitor control.

| Bits | Field | Access | Reset | Description |
|------|-------|--------|-------|-------------|
| 0 | ENABLE | R/W | 0 | 1 = Capture transactions, 0 = Stop capture |
| 1 | CLEAR | W | 0 | Write 1 to clear FIFO. Self-clearing. |
| 31:2 | Reserved | RO | 0 | Reserved |

**Behavior:**
- When ENABLE=1, inbound memory write transactions to BAR0 space are captured.
- Writing CLEAR=1 empties the FIFO.
- Captures include: address, data, byte enables, and transaction attributes.

---

## 5. Internal DMA Buffer

### 5.1 Requirements

The exerciser must contain an internal memory buffer of sufficient size to:
- Hold data written by DMA-to-device operations
- Source data for DMA-from-device operations

### 5.2 Minimum Size

- **Minimum:** 4KB
- **Recommended:** 16KB or larger

### 5.3 Behavior

| Operation | Description |
|-----------|-------------|
| DMA To Device | Host memory → Internal buffer (data stored) |
| DMA From Device | Internal buffer → Host memory (data retrieved) |

Tests typically:
1. DMA data from host to exerciser (store)
2. DMA data from exerciser back to host (retrieve)
3. Compare to verify round-trip integrity

---

## 6. Transaction Monitor Requirements

### 6.1 Purpose

The transaction monitor captures inbound memory write transactions to enable verification that:
- PE (CPU) 2-byte writes arrive as 2-byte transactions
- PE 4-byte writes arrive as 4-byte transactions  
- PE 8-byte writes arrive as 8-byte transactions
- Transaction ordering is preserved

### 6.2 Capture Requirements

For each inbound memory write to BAR0, capture:

| Field | Size | Description |
|-------|------|-------------|
| Address | 64 bits | Full address within BAR |
| Data | 64 bits | Write data |
| First Byte Enable | 4 bits | Byte enables for first DW |
| Last Byte Enable | 4 bits | Byte enables for last DW |
| Transaction Type | 1 bit | Read/Write |
| Length | 16 bits | Transaction length in bytes |

### 6.3 FIFO Requirements

| Parameter | Requirement |
|-----------|-------------|
| Depth | Minimum 16 transactions |
| Width | 5 × 32-bit words per entry |
| Overflow | Discard newest on overflow |

### 6.4 Critical Implementation Note

**The byte enable capture is essential.** Tests verify that sub-DWORD writes from the CPU arrive with correct byte enables, not as read-modify-write sequences. The exerciser must:

1. Capture the actual TLP byte enables received from PCIe
2. Not synthesize or modify byte enables
3. Report exact transaction granularity to software

---

## 7. PCIe Extended Capabilities

The exerciser must implement these PCIe extended capabilities in configuration space (offset ≥ 0x100):

### 7.1 Required Capabilities

| Capability | ID | Purpose |
|------------|-------|---------|
| AER (Advanced Error Reporting) | 0x0001 | Error injection/detection tests |
| PASID | 0x001B | PASID transaction tests |
| ATS (Address Translation Services) | 0x000F | ATS translation tests |
| ACS (Access Control Services) | 0x000D | P2P isolation tests |

### 7.2 Vendor-Specific Capability (DVSEC)

A Designated Vendor-Specific Extended Capability (DVSEC, ID 0x0023) is required for error injection control.

**DVSEC Structure:**

| Offset | Field | Description |
|--------|-------|-------------|
| +0x00 | Header | Standard DVSEC header |
| +0x08 | DVSEC_CTRL | Error injection control |

**DVSEC_CTRL Register:**

| Bits | Field | Description |
|------|-------|-------------|
| 16:0 | Reserved | - |
| 17 | ERROR_INJECT | Write 1 to inject configured error |
| 18 | POISON_MODE | Enable poison data forwarding |
| 30:19 | Reserved | - |
| 31 | FATAL | 0 = Non-fatal, 1 = Fatal error |
| 30:20 | ERR_CODE | Error type code |

---

## 8. Interrupt Requirements

### 8.1 MSI-X Support (Recommended)

| Parameter | Requirement |
|-----------|-------------|
| Table Size | Minimum 32 vectors, up to 2048 |
| Table Location | Dedicated BAR (BAR2 recommended) |
| PBA Location | Dedicated BAR or shared with table |
| Masking | Per-vector mask support required |

### 8.2 Legacy Interrupt Support

- Must support INTx assertion/deassertion via INTXCTL register
- Must properly signal interrupt through PCIe INTx mechanism

### 8.3 Interrupt Generation Behavior

When MSICTL.TRIGGER is written:
1. Read vector entry from MSI-X table (address + data)
2. If vector is not masked: generate memory write TLP to address with data
3. If vector is masked: set corresponding bit in PBA

---

## 9. DMA Engine Requirements

### 9.1 Supported Operations

| Operation | TLP Type | Description |
|-----------|----------|-------------|
| To Device | Memory Read | Read from host, store in internal buffer |
| From Device | Memory Write | Write from internal buffer to host |

### 9.2 TLP Attribute Control

The DMA engine must generate TLPs with configurable:

| Attribute | Control Register | TLP Field |
|-----------|------------------|-----------|
| No Snoop | DMACTL1[5] | Attr[0] |
| Relaxed Ordering | (Optional) | Attr[1] |
| Address Type | DMACTL1[11:10] | AT[1:0] |
| PASID | PASID_VAL when DMACTL1[6]=1 | PASID TLP Prefix |
| Requester ID | RID_CTL when RID_CTL[31]=1 | Requester ID field |

### 9.3 Completion Handling

- Must properly handle split completions
- Must handle completion timeouts (set ERROR status)
- Must handle error completions (UR, CA)

### 9.4 Max Payload Handling

- Must respect Max_Payload_Size from Device Control register
- Must split large transfers into compliant TLPs

---

## 10. ATS Requirements

### 10.1 ATS Translation Request

When ATSCTL.START is written:
1. Generate ATS Translation Request TLP
2. Wait for Translation Completion
3. Store translated address in ATS_ADDR
4. Set ATSCTL.COMPLETE and ATSCTL.SUCCESS appropriately

### 10.2 Using Translated Addresses

When DMACTL1.ADDR_TYPE = 10 (Translated):
- DMA uses address from DMA_BUS_ADDR (which software sets to translated address)
- TLP includes AT=10 indicating pre-translated address

---

## 11. PASID Requirements

### 11.1 PASID TLP Prefix

When DMACTL1.PASID_EN = 1:
- All outbound memory transactions include PASID TLP Prefix
- PASID value from PASID_VAL register
- PASID width from DMACTL1.PASID_LEN (value + 16 bits)

### 11.2 PASID Capability

The PASID Extended Capability must report:
- Max PASID Width (minimum 16 bits, up to 20 bits)
- Execute Permission Supported (optional)
- Privileged Mode Supported (optional)

---

## 12. Error Injection Requirements

### 12.1 Supported Error Types

The exerciser should support injection of:

| Error | Description |
|-------|-------------|
| Correctable | CRC error, etc. |
| Non-Fatal | Completion timeout, etc. |
| Fatal | Malformed TLP, etc. |
| Poisoned Data | TLP with EP bit set |

### 12.2 Poison Data Forwarding

When DVSEC_CTRL.POISON_MODE = 1:
- DMA write TLPs are sent with EP (Error/Poison) bit set
- Tests RAS error handling in system

---

## 13. RAS Interface

### 13.1 RAS Register Block (Offset 0x10000 from BAR0)

| Offset | Register | Description |
|--------|----------|-------------|
| 0x10000 | RAS_CTRL | RAS control |
| 0x10008 | RAS_STATUS | RAS status/error record |

### 13.2 RAS_CTRL

| Bits | Field | Description |
|------|-------|-------------|
| 0 | ENABLE | Enable RAS error recording |
| 31:1 | Reserved | - |

### 13.3 RAS_STATUS

| Bits | Field | Description |
|------|-------|-------------|
| 31:0 | STATUS | Error status bits |

---

## 14. Reset and Initialization

### 14.1 Reset State

On reset (PCIe fundamental reset or function-level reset):
- All control registers return to reset values (0)
- Internal DMA buffer contents are undefined
- Transaction monitor FIFO is cleared
- Pending DMAs are aborted

### 14.2 Initialization Sequence

Software initialization:
1. Enable Bus Master and Memory Space in Command register
2. Program BAR0 base address
3. Configure MSI-X if used
4. Exerciser is ready for use

---

## 15. Test Coverage Summary

This exerciser enables the following BSA/SBSA compliance tests:

| Test ID | Description | Exerciser Features Used |
|---------|-------------|-------------------------|
| PCI_PP_04 | P2P ACS Functionality | DMA, RID override |
| PCI_IC_11 | I/O Coherency | DMA, No Snoop control |
| PCI_LI_02 | Legacy Interrupt | INTXCTL |
| ITS_DEV_6 | ITS GITS_TRANSLATER | MSI generation |
| S_PCIe_03 | PE 2/4/8B writes | Transaction monitor, byte enables |
| S_PCIe_04 | Targeted writes | Transaction monitor |
| RI_SMU_1 | Address translation | ATS, DMA |
| RI_SMU_3 | PASID transactions | PASID, DMA |
| PCI_ER_* | Error handling | Error injection, RAS |
| PCI_MSI_2 | MSI(-X) unique ID | MSI-X generation |

---

## Appendix A: Quick Reference - Register Map

```
BAR0 + 0x000: MSICTL         [31:TRIGGER, 10:0:VECTOR_ID]
BAR0 + 0x004: INTXCTL        [0:ASSERT]
BAR0 + 0x008: DMACTL         [11:10:AT, 9:USE_ATC, 8:INSTR, 7:PRIV, 6:PASID_EN, 5:NO_SNOOP, 4:DIR, 3:0:TRIGGER]
BAR0 + 0x00C: DMA_OFFSET     [31:0] Offset within BAR1
BAR0 + 0x010: DMA_BUS_ADDR_LO [31:0]
BAR0 + 0x014: DMA_BUS_ADDR_HI [31:0]
BAR0 + 0x018: DMA_LEN        [31:0]
BAR0 + 0x01C: DMASTATUS      [2:CLEAR, 1:0:STATUS]
BAR0 + 0x020: PASID_VAL      [19:0:PASID]
BAR0 + 0x024: ATSCTL         [9:INVALIDATED, 8:CACHEABLE, 7:SUCCESS, 6:IN_FLIGHT, 5:CLEAR_ATC, 4:EXEC_REQ, 3:PASID_EN, 2:NO_WRITE, 1:PRIV, 0:TRIGGER]
BAR0 + 0x028: ATS_ADDR_LO    [31:0]
BAR0 + 0x02C: ATS_ADDR_HI    [31:0]
BAR0 + 0x030: ATS_RANGE_SIZE [31:0] Size in bytes
BAR0 + 0x038: ATS_PERM       [6:READ_PRIV, 4:WRITE_PRIV, 3:EXEC_PRIV, 2:READ, 1:WRITE, 0:EXEC]
BAR0 + 0x03C: RID_CTL        [31:VALID, 15:0:REQ_ID]
BAR0 + 0x040: TXN_TRACE      [31:0] (FIFO read, 0xFFFFFFFF = empty)
BAR0 + 0x044: TXN_CTRL       [1:CLEAR, 0:ENABLE]
```

---

## Appendix B: Transaction Trace Record Format

```
Word 0: TX_ATTRIBUTES
  [0]     - Type (defaults to 0)
  [1]     - R/W (1=Read, 0=Write)
  [2]     - CFG/MEM (1=Config, 0=Memory)
  [15:3]  - Reserved
  [31:16] - Size (one-hot: bit N = 2^N bytes)

Word 1: ADDRESS[31:0]
Word 2: ADDRESS[63:32]
Word 3: DATA[31:0]
Word 4: DATA[63:32]
```

---

## Appendix C: DMA Control Bit Summary

```
DMACTL[3:0]    TRIGGER     Write 0x1 to begin DMA (self-clearing)
DMACTL[4]      DIRECTION   0=Read from host, 1=Write to host
DMACTL[5]      NO_SNOOP    0=Snoop, 1=No-Snoop attribute
DMACTL[6]      PASID_EN    Include PASID prefix
DMACTL[7]      PRIVILEGED  Privileged access mode (requires PASID_EN=1)
DMACTL[8]      INSTRUCTION Instruction access (requires PASID_EN=1)
DMACTL[9]      USE_ATC     Use ATC for address translation
DMACTL[11:10]  ADDR_TYPE   0=Default, 1=Untranslated, 2=Translated, 3=Reserved
```

---

**End of Specification**
