# BSA PCIe Exerciser - Implementation Status

**Last Updated:** December 2025

This document tracks the implementation status of the ARM BSA PCIe Exerciser.

---

## Summary

| Component | Status | Notes |
|-----------|--------|-------|
| Multi-BAR Routing | ✅ Complete | BAR0/1/2/5 configured, bar_hit dispatch |
| Device ID | ✅ Complete | 0x13B5:0xED01 (ARM BSA Exerciser) |
| BAR0 Registers | ✅ Complete | Full BSA spec register map via Wishbone |
| MSI-X (2048 vectors) | ✅ Complete | Table in BAR2, PBA in BAR5 |
| DMA Engine | ✅ Complete | Register-triggered, 16KB buffer in BAR1 |
| Transaction Monitor | ✅ Complete | TXN_TRACE FIFO, 32 entry capture buffer |
| Legacy Interrupt | ✅ Complete | INTx assertion via INTXCTL |
| ATS Engine | ❌ Not Started | Translation requests |
| PASID Support | ❌ Not Started | TLP prefix generation |
| RID Override | ❌ Not Started | Requester ID spoofing |

---

## Completed Features

### Multi-BAR Infrastructure

**Location:** `src/bsa_pcie_exerciser/core/`

- `LitePCIeMultiBAREndpoint` - Routes requests based on `bar_hit`
- `LitePCIeBARDispatcher` - Demuxes requests to per-BAR handlers
- `LitePCIeCompletionArbiter` - Muxes completions back
- `LitePCIeMasterArbiter` - Round-robin for outbound DMA/MSI-X
- `LitePCIeStubBARHandler` - Returns UR for disabled BARs

**BAR Configuration:**
| BAR | Size | Purpose | Handler |
|-----|------|---------|---------|
| 0 | 4KB | CSR Registers | BSARegisters (Wishbone) |
| 1 | 16KB | DMA Buffer | BSADMABufferHandler |
| 2 | 32KB | MSI-X Table | LitePCIeMSIXTable |
| 3 | - | Disabled | - |
| 4 | - | Disabled | - |
| 5 | 4KB | MSI-X PBA | LitePCIeMSIXPBA |

### Device Identification

**Location:** `src/bsa_pcie_exerciser/bsa_pcie_exerciser.py`

- Vendor ID: 0x13B5 (ARM Ltd.)
- Device ID: 0xED01 (BSA Exerciser)
- Class Code: 0xFF0000 (Unclassified)
- Subsystem: 0x13B5:0xED01

### BAR0 Register Map

**Location:** `src/bsa_pcie_exerciser/core/bsa_registers.py`

Implements full ARM BSA Exerciser register map with explicit address decoding via Wishbone slave interface. Addresses match the ARM spec exactly, including gaps.

See `docs/REGISTER_MAP.md` for detailed register definitions.

### MSI-X Subsystem

**Location:** `src/bsa_pcie_exerciser/msix/`

- `LitePCIeMSIXTable` - BAR2 handler, 2048 vectors, byte-enable writes
- `LitePCIeMSIXPBA` - BAR5 handler, pending bit array
- `LitePCIeMSIXController` - Memory Write TLP generation
- Software trigger via MSICTL register

### DMA Engine

**Location:** `src/bsa_pcie_exerciser/dma/`

- `BSADMABuffer` - 16KB dual-port BRAM in BAR1
- `BSADMABufferHandler` - PCIe read/write access to buffer
- `BSADMAEngine` - Register-triggered DMA transfers

**Features:**
- Read from host (host → buffer): Issues Memory Read TLPs
- Write to host (buffer → host): Issues Memory Write TLPs
- TLP attributes: No-Snoop, Address Type
- Splits large transfers at max_request_size boundary
- Completion handling with timeout detection

**Control Interface:**
- Triggered via DMACTL register
- Address from DMA_BUS_ADDR registers
- Length from DMA_LEN register
- Buffer offset from DMA_OFFSET register
- Status reported in DMASTATUS register

### Transaction Monitor

**Location:** `src/bsa_pcie_exerciser/monitor/`

- `TransactionMonitor` - Captures inbound PCIe requests to FIFO
- Taps depacketizer request stream (non-invasive)
- 32-entry circular buffer, 160 bits per transaction (BSA spec max)
- 5 x 32-bit word read sequence via TXN_TRACE register

**Capture Layout (Word 0 - Attributes):**
- [0]: we (1=write, 0=read)
- [3:1]: bar_hit[2:0]
- [13:4]: len[9:0]
- [17:14]: first_be[3:0]
- [21:18]: last_be[3:0]
- [23:22]: attr[1:0]
- [25:24]: at[1:0]

**Words 1-4:** ADDRESS[63:0], DATA[63:0]

**Control:**
- TXN_CTRL[0]: Enable capture
- TXN_CTRL[1]: Clear FIFO (auto-clears)
- TXN_TRACE: Returns 0xFFFFFFFF when empty

---

### Legacy Interrupt

**Location:** `src/bsa_pcie_exerciser/core/intx_controller.py`

- `INTxController` - FSM for Xilinx PCIe INTx handshake
- Patched S7PCIEPHY with `intx_req`, `intx_assert`, `intx_rdy` signals
- Assert/deassert via INTXCTL[0] register

**Protocol:**
- Monitors INTXCTL[0] for state changes
- Pulses `cfg_interrupt` with desired `cfg_interrupt_assert` value
- Waits for `cfg_interrupt_rdy` acknowledgement

---

## Remaining Work

### Phase 5: Advanced Features (Lower Priority)

**ATS Engine:**
- [ ] Generate ATS Translation Request TLPs
- [ ] Handle Translation Completions
- [ ] Store results in ATS_ADDR/ATS_RANGE/ATS_PERM registers
- [ ] ATC (Address Translation Cache)

**PASID Support:**
- [ ] Generate PASID TLP Prefix when enabled
- [ ] Use PASID_VAL register value
- [ ] Privileged/Execute mode bits

**RID Override:**
- [ ] Use custom Requester ID when RID_CTL.valid=1
- [ ] Modify outbound TLP headers

**Error Injection:**
- [ ] DVSEC for error control
- [ ] Poison mode for completion errors

---

## LitePCIe Dependencies

Uses forked LitePCIe with `feature/tlp-attributes` branch:
- `bar_hit` extraction from PHY
- `first_be`/`last_be` propagation
- `attr` field in request/packetizer
- `intx_req`/`intx_assert`/`intx_rdy` for legacy interrupts

Repository: https://github.com/sjalloq/litepcie/tree/feature/tlp-attributes

---

## Test Infrastructure

### MSI-X Tests

**Location:** `tests/msix/`

Cocotb testbench with 15 tests covering:
- Table read/write operations
- Software-triggered MSI-X generation
- Masked vector handling
- PBA read-only enforcement
- Backpressure handling
- Multi-vector operations

### Running Tests

```bash
source sourceme
cd tests/msix
make
```

---

## Build

```bash
source sourceme
make build      # Build bitstream (requires Vivado)
make load       # Load via JTAG
```

---

## Target Hardware

- SPEC-A7 (Xilinx Artix-7 xc7a35t)
- PCIe Gen2 x1
- 125MHz system clock
