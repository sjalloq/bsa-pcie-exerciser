# BSA PCIe Exerciser - Implementation Status

**Last Updated:** January 2026

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
| ATS Engine | ✅ Complete | Translation requests, ATC, invalidation |
| PASID Support | ✅ Complete | TLP prefix generation |
| RID Override | ✅ Complete | Requester ID override |
| USB Monitor | ✅ Complete | TLP streaming via FT601 |
| Squirrel Platform | ✅ Complete | CaptainDMA/Squirrel board support |

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

### ATS Engine

**Location:** `src/bsa_pcie_exerciser/gateware/ats/`

- `ATSEngine` - Generates ATS Translation Request TLPs
- `ATSCache` - Address Translation Cache with PASID support
- `ATSInvalidationHandler` - Processes invalidation requests

**Features:**
- Translation request generation via ATSCTL register
- ATC with configurable entries and PASID matching
- Invalidation handling (global and page-selective)
- Results stored in ATS_ADDR/ATS_RANGE/ATS_PERM registers

### PASID Support

**Location:** `src/bsa_pcie_exerciser/gateware/pasid/`

- `PASIDPrefixInjector` - Injects PASID TLP prefix on outbound requests
- Controlled via DMACTL register bits (pasid_en, privileged, instruction)
- PASID value from PASID_VAL register

### Requester ID Override

**Location:** `src/bsa_pcie_exerciser/gateware/core/bsa_registers.py`

- RID_CTL register for custom Requester ID
- When valid=1, outbound TLPs use override value

### USB Monitor Subsystem

**Location:** `src/bsa_pcie_exerciser/gateware/usb/monitor/`

- `TLPCaptureEngine` - Captures RX/TX TLPs with timestamps
- `MonitorAsyncFIFO` - CDC between PCIe and USB clock domains
- `MonitorPacketArbiter` - Round-robin packet-atomic arbitration
- `USBMonitorSubsystem` - Top-level integration

**Features:**
- Full TLP capture (RX and TX directions)
- Streaming via FT601 USB 3.0 interface
- Etherbone CSR access via USB channel 0
- Monitor data via USB channel 1
- Drop counting and statistics

---

## Remaining Work

### Future Enhancements (Lower Priority)

**Error Injection:**
- [ ] DVSEC for error control
- [ ] Poison mode for completion errors

**Host Software Tools:**
- [ ] Wireshark dissector for USB capture
- [ ] Extended analysis tooling

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

### Test Directories

| Directory | Purpose | Tests |
|-----------|---------|-------|
| `tests/integration/` | PCIe path integration | ~45 tests |
| `tests/usb/` | USB testbench (Squirrel) | ~40 tests |
| `tests/dma/` | DMA unit tests | 9 tests |
| `tests/msix/` | MSI-X unit tests | 15 tests |

### USB Testbench (`tests/usb/`)

Comprehensive testbench for USB subsystem:
- `test_etherbone.py` - CSR access via USB
- `test_monitor_rx.py` - RX TLP capture
- `test_monitor_tx.py` - TX TLP capture
- `test_golden_reference.py` - Field verification
- `test_stress.py` - High-volume stress tests
- `test_corner_cases.py` - Edge cases
- `test_ats_invalidation.py` - ATS invalidation
- `test_pasid_switching.py` - PASID context switching
- `test_requester_id.py` - RID override
- `test_dma_ordering.py` - Multi-DMA ordering

### Running Tests

```bash
source sourceme
cd tests/usb
make sim

# Or specific tests:
make sim COCOTB_TEST_FILTER=test_etherbone
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
