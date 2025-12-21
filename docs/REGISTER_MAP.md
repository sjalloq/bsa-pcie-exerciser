# BSA Exerciser Register Map

## Overview

The BSA Exerciser BAR0 register map follows the ARM BSA Exerciser specification.
All registers are 32-bit, little-endian, accessed via PCIe BAR0.

**Implementation:** `src/bsa_pcie_exerciser/core/bsa_registers.py`

---

## Register Summary

| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| 0x000 | MSICTL | RW | MSI-X trigger control |
| 0x004 | INTXCTL | RW | Legacy interrupt control |
| 0x008 | DMACTL | RW | DMA control |
| 0x00C | DMA_OFFSET | RW | DMA buffer offset |
| 0x010 | DMA_BUS_ADDR_LO | RW | DMA bus address [31:0] |
| 0x014 | DMA_BUS_ADDR_HI | RW | DMA bus address [63:32] |
| 0x018 | DMA_LEN | RW | DMA transfer length |
| 0x01C | DMASTATUS | RW | DMA status |
| 0x020 | PASID_VAL | RW | PASID value |
| 0x024 | ATSCTL | RW | ATS control |
| 0x028 | ATS_ADDR_LO | RO | ATS translated address [31:0] |
| 0x02C | ATS_ADDR_HI | RO | ATS translated address [63:32] |
| 0x030 | ATS_RANGE_SIZE | RO | ATS translated range size |
| 0x038 | ATS_PERM | RO | ATS reply permissions |
| 0x03C | RID_CTL | RW | Requester ID override |
| 0x040 | TXN_TRACE | RO | Transaction trace FIFO |
| 0x044 | TXN_CTRL | RW | Transaction monitor control |
| 0x048 | ID | RO | Exerciser ID |

---

## Register Definitions

### 0x000: MSICTL - MSI-X Control

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 10:0 | vector_id | RW | MSI-X vector index (0-2047) |
| 30:11 | reserved | RO | Reserved |
| 31 | trigger | RW | Write 1 to generate MSI-X. Self-clearing. |

**Behavior:** Writing with trigger=1 generates an MSI-X for the specified vector.
The trigger bit auto-clears after the MSI-X is sent.

---

### 0x004: INTXCTL - Legacy Interrupt Control

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 0 | assert | RW | 1 = Assert INTx, 0 = Deassert |
| 31:1 | reserved | RO | Reserved |

---

### 0x008: DMACTL - DMA Control

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 3:0 | trigger | RW | Write 0x1 to start DMA. Self-clearing. |
| 4 | direction | RW | 0 = Read from host, 1 = Write to host |
| 5 | no_snoop | RW | 0 = Snoop, 1 = No-Snoop attribute |
| 6 | pasid_en | RW | 1 = Include PASID TLP prefix |
| 7 | privileged | RW | 1 = Privileged access mode |
| 8 | instruction | RW | 1 = Instruction access |
| 9 | use_atc | RW | 1 = Use ATC for address translation |
| 11:10 | addr_type | RW | Address Type: 0=Default, 1=Untranslated, 2=Translated |
| 31:12 | reserved | RO | Reserved |

**Behavior:** Writing trigger=1 starts a DMA transfer using the configured
address, length, and offset. The trigger field auto-clears when DMA completes.

---

### 0x00C: DMA_OFFSET - DMA Buffer Offset

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | offset | RW | Byte offset within BAR1 buffer |

---

### 0x010: DMA_BUS_ADDR_LO - DMA Bus Address Low

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | addr_lo | RW | Host memory address [31:0] |

---

### 0x014: DMA_BUS_ADDR_HI - DMA Bus Address High

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | addr_hi | RW | Host memory address [63:32] |

---

### 0x018: DMA_LEN - DMA Transfer Length

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | length | RW | Transfer length in bytes |

---

### 0x01C: DMASTATUS - DMA Status

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 1:0 | status | RO | 0=OK, 1=Range error, 2=Timeout/Internal error |
| 2 | clear | WO | Write 1 to clear status |
| 31:3 | reserved | RO | Reserved |

---

### 0x020: PASID_VAL - PASID Value

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 19:0 | pasid | RW | 20-bit PASID value |
| 31:20 | reserved | RO | Reserved |

---

### 0x024: ATSCTL - ATS Control

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 0 | trigger | WO | Write 1 to send ATS request. Self-clearing. |
| 1 | privileged | RW | 1 = Privileged access |
| 2 | no_write | RW | 1 = Request read-only permission |
| 3 | pasid_en | RW | 1 = Include PASID in ATS request |
| 4 | exec_req | RW | 1 = Request execute permission |
| 5 | clear_atc | W1C | Write 1 to clear ATC |
| 6 | in_flight | RO | 1 = ATS request in progress |
| 7 | success | RO | 1 = Translation successful |
| 8 | cacheable | RO | 1 = Result is cacheable |
| 9 | invalidated | RO | 1 = ATC was invalidated |
| 31:10 | reserved | RO | Reserved |

---

### 0x028: ATS_ADDR_LO - ATS Translated Address Low

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | addr_lo | RO | Translated address [31:0] |

---

### 0x02C: ATS_ADDR_HI - ATS Translated Address High

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | addr_hi | RO | Translated address [63:32] |

---

### 0x030: ATS_RANGE_SIZE - ATS Translated Range Size

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | size | RO | Size of translated region in bytes |

---

### 0x038: ATS_PERM - ATS Reply Permissions

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 0 | exec | RO | Execute permission granted |
| 1 | write | RO | Write permission granted |
| 2 | read | RO | Read permission granted |
| 3 | exec_priv | RO | Execute permission (privileged) |
| 4 | write_priv | RO | Write permission (privileged) |
| 5 | reserved | RO | Reserved |
| 6 | read_priv | RO | Read permission (privileged) |
| 31:7 | reserved | RO | Reserved |

---

### 0x03C: RID_CTL - Requester ID Override

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 15:0 | req_id | RW | Custom Requester ID value |
| 30:16 | reserved | RO | Reserved |
| 31 | valid | RW | 1 = Use custom Requester ID |

---

### 0x040: TXN_TRACE - Transaction Trace FIFO

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | data | RO | Next FIFO word (0xFFFFFFFF = empty) |

**Behavior:** Each captured transaction is 5 words. Read 5 times to get one
complete transaction record. Returns 0xFFFFFFFF when FIFO is empty.

**Transaction Record Format:**
- Word 0: TX_ATTRIBUTES (type, R/W, size)
- Word 1: ADDRESS[31:0]
- Word 2: ADDRESS[63:32]
- Word 3: DATA[31:0]
- Word 4: DATA[63:32]

---

### 0x044: TXN_CTRL - Transaction Monitor Control

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 0 | enable | RW | 1 = Capture transactions |
| 1 | clear | W1C | Write 1 to clear FIFO. Self-clearing. |
| 31:2 | reserved | RO | Reserved |

---

### 0x048: ID - Exerciser ID

| Bits | Name | Access | Description |
|------|------|--------|-------------|
| 31:0 | id | RO | 0xED0113B5 (Device ID << 16 | Vendor ID) |

---

## Usage Examples

### Trigger MSI-X Vector 5

```c
// Write vector ID with trigger bit
write32(BAR0 + 0x000, (1 << 31) | 5);
```

### DMA Read from Host (host → buffer)

```c
// Configure transfer
write32(BAR0 + 0x010, host_addr & 0xFFFFFFFF);  // Bus address low
write32(BAR0 + 0x014, host_addr >> 32);          // Bus address high
write32(BAR0 + 0x018, 256);                      // 256 bytes
write32(BAR0 + 0x00C, 0);                        // Buffer offset 0

// Start DMA read (direction=0, trigger=1)
write32(BAR0 + 0x008, 0x01);

// Poll for completion
while (read32(BAR0 + 0x01C) & 0x3 == 0)
    ;  // Wait for status
```

### DMA Write to Host with No-Snoop (buffer → host)

```c
// Configure transfer
write32(BAR0 + 0x010, host_addr & 0xFFFFFFFF);
write32(BAR0 + 0x014, host_addr >> 32);
write32(BAR0 + 0x018, 128);
write32(BAR0 + 0x00C, 0x100);  // Buffer offset 0x100

// Start DMA write with no-snoop (direction=1, no_snoop=1, trigger=1)
write32(BAR0 + 0x008, 0x31);
```

---

## Reference

ARM BSA Exerciser Specification:
- Source: `external/sysarch-acs/docs/pcie/Exerciser.md`
- Hardware Spec: `docs/specs/arm_bsa_pcie_exerciser_hardware_spec.md`
