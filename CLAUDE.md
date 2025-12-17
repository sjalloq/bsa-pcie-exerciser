# CLAUDE.md

## Project Overview

Arm BSA/SBSA PCIe Exerciser implementation using the LiteX/LitePCIe framework. This is an FPGA-based PCIe endpoint for compliance testing of SMMU/IOMMU, cache coherency, address translation, and interrupt handling.

Target hardware: 
- SPEC-A7 board (Xilinx Artix-7 xc7a50t)
- CaptainDMA 4.1th
- LambdaConcept Screamer PCIe Squirrel

Note that many of the Artix-7 PCILeech cards may prove to be great targets.  They range from PCIe card form factors to M.2 cards and they provide a range of link configurations from x1 to x4.  They tend to be quite cheap compared to higher end dev kits or NICs such as the SPEC-A7.

## LiteX Support

The backbone of this project is the LiteX framework and the existing support for the SPEC-A7 and Screamer.  LiteX provides a PCIe implementation that we can build upon.  I have already forked the project and added the necessary support for BAR hit logic.

- **LambdaConcept PCIe Screamer**: https://github.com/enjoy-digital/pcie_screamer
- **SPEC-A7**: https://github.com/enjoy-digital/litex_wr_nic
- **LitePCIe**: https://github.com/sjalloq/litepcie/tree/feature/tlp-attributes

## Arm BSA

The Arm Base System Architecture defines a set of hardware capabilities as seen by software running on Arm 64-bit apps processors.  This project is interested in the BSA PCIe Exerciser, a PCIe endpoint designed to aid validation of PCIe subsystems.  

- **Main GitHub Page**: https://github.com/ARM-software/sysarch-acs
- **PCIe Exerciser**: https://github.com/ARM-software/sysarch-acs/blob/main/docs/pcie/Exerciser.md

## Build Commands

The project uses a `sourceme` to set up the environment which creates a Python virtual env and installs all dependencies.  Whenever you need to use Python, always use the venv.

```bash
# Setup environment
source sourceme

# Build bitstream (requires Vivado)
make build

# Or directly:
bsa-pcie-exerciser --build

# Load via JTAG
bsa-pcie-exerciser --load
```

## Architecture

### SoC Structure (`src/bsa_pcie_exerciser/bsa_pcie_exerciser.py`)

The top-level `BSAExerciserSoC` extends LiteX's `SoCMini`:
- **CRG**: Clock reset generator using 125MHz board oscillator
- **PCIe PHY**: S7PCIEPHY for Xilinx 7-series, Gen2 x1
- **Multi-BAR Endpoint**: Custom endpoint with per-BAR crossbars
  - BAR0: CSRs (4KB) via Wishbone bridge
  - BAR1: DMA Buffer (16KB) - reserved
  - BAR2: MSI-X Table (32KB for 2048 vectors) - reserved
  - BAR5: MSI-X PBA (4KB) - reserved

### LitePCIe Extensions (`src/bsa_pcie_exerciser/litepcie/`)

- `multibar_endpoint.py`: `LitePCIeMultiBAREndpoint` - routes requests to per-BAR crossbars using `bar_hit` field
- `bar_routing.py`: BAR dispatcher, completion arbiter, master arbiter, stub handlers

### Platform (`src/bsa_pcie_exerciser/platform/spec_a7_platform.py`)

SPEC-A7 board definition with pin mappings for PCIe, clocks, LEDs, SFPs, etc.

## LiteX/Migen Patterns

- Use `LiteXModule` base class for automatic CSR collection - assigning `self.foo = SomeModule()` auto-registers CSRs
- CSRs: `CSRStorage` (read/write), `CSRStatus` (read-only)
- Clock domains: `self.cd_sys = ClockDomain()`
- Combinational: `self.comb += [...]`
- Synchronous: `self.sync += [...]`
- Stream connections: `source.connect(sink)`

## Project Phases

1. Basic PCIe endpoint with CSR access (current)
2. Multi-BAR support with routing infrastructure
3. MSI-X with 2048 vectors
4. BSA DMA engine with TLP attribute control
5. PASID/ATS support
