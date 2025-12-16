# BSA Exerciser Register Map

## Overview

The BSA Exerciser is accessed via PCIe BAR0. All registers are 32-bit aligned.

## Register Summary

| Offset | Name | Access | Description |
|--------|------|--------|-------------|
| 0x00 | MSI_CONTROL | RW | MSI trigger control |
| 0x04 | MSI_VECTOR | RW | MSI vector index |
| 0x08 | DMA_CONTROL | RW | DMA control and trigger |
| 0x0C | DMA_LOCAL_OFFSET | RW | Local memory offset |
| 0x10 | DMA_BUS_ADDR_LO | RW | Bus address [31:0] |
| 0x14 | DMA_BUS_ADDR_HI | RW | Bus address [63:32] |
| 0x18 | DMA_LENGTH | RW | Transfer length in bytes |
| 0x1C | DMA_STATUS | RO | DMA status |
| 0x20 | PASID_VALUE | RW | PASID value (Phase 2) |
| 0x24 | AT_CONTROL | RW | ATS control (Phase 3) |
| 0x40 | TXN_COUNT | RO | Transaction count |
| 0x44 | TXN_ENTRY | RO | Transaction trace entry |
| 0xFC | ID | RO | Exerciser ID (0xBSA00001) |

---

## Register Definitions

### 0x00: MSI_CONTROL

| Bits | Name | Description |
|------|------|-------------|
| 15:0 | vector | MSI vector index to trigger |
| 16 | trigger | Write 1 to trigger MSI |

### 0x08: DMA_CONTROL

| Bits | Name | Description |
|------|------|-------------|
| 3:0 | trigger | Write 0x1 to trigger DMA |
| 4 | direction | 0=Read (host→EP), 1=Write (EP→host) |
| 5 | no_snoop | No-Snoop attribute (NS) |
| 6 | relaxed_ord | Relaxed Ordering attribute (RO) |
| 7 | reserved | - |
| 8 | pasid_en | Enable PASID prefix (Phase 2) |
| 9 | privileged | PASID PMR bit (Phase 2) |
| 10 | instruction | PASID Execute bit (Phase 2) |
| 11 | reserved | - |
| 12 | use_atc | Use ATC for translation (Phase 3) |
| 14:13 | addr_type | Address Type: 0=Untrans, 1=TransReq, 2=Trans |

### 0x1C: DMA_STATUS

| Bits | Name | Description |
|------|------|-------------|
| 0 | busy | Transfer in progress |
| 1 | done | Transfer complete (write 1 to clear) |
| 2 | error | Completion error occurred |
| 6:4 | cpl_status | Last completion status (PCIe defined) |

### 0x20: PASID_VALUE (Phase 2)

| Bits | Name | Description |
|------|------|-------------|
| 19:0 | pasid | 20-bit PASID value |

---

## Usage Examples

### Basic DMA Write (EP → Host)

```python
# Configure DMA
write_reg(0x10, 0x12340000)  # Bus address low
write_reg(0x14, 0x00000001)  # Bus address high (64-bit)
write_reg(0x18, 256)         # 256 bytes
write_reg(0x0C, 0)           # Local offset

# Trigger write with no-snoop
write_reg(0x08, 0x31)        # trigger=1, direction=1(write), no_snoop=1

# Wait for completion
while (read_reg(0x1C) & 0x1):  # busy
    pass

# Check status
status = read_reg(0x1C)
assert status & 0x2  # done
assert not (status & 0x4)  # no error
```

### DMA Read (Host → EP)

```python
# Configure
write_reg(0x10, 0x56780000)
write_reg(0x14, 0x00000000)
write_reg(0x18, 128)
write_reg(0x0C, 0x100)

# Trigger read (direction=0)
write_reg(0x08, 0x01)

# Wait and check
while (read_reg(0x1C) & 0x1):
    pass
```

### Trigger MSI-X Vector

```python
# Trigger MSI vector 5
write_reg(0x04, 5)           # Vector index
write_reg(0x00, 0x10000)     # Trigger bit
```

---

## Compliance with BSA Spec

This register layout follows the BSA Exerciser specification from:
https://github.com/ARM-software/bsa-acs/blob/main/docs/PCIe_Exerciser/Exerciser.md

Key mappings:

| BSA PAL Function | Register |
|------------------|----------|
| `pal_exerciser_set_param(SNOOP)` | DMA_CONTROL.no_snoop |
| `pal_exerciser_ops(START_DMA)` | DMA_CONTROL.trigger |
| `pal_exerciser_get_param(TRANSACTION)` | TXN_ENTRY |
