# BSA PCIe Exerciser

[![License](https://img.shields.io/badge/License-BSD_3--Clause-blue.svg)](LICENSE)
[![Status](https://img.shields.io/badge/Status-Work_in_Progress-yellow.svg)]()

> ⚠️ **Work in Progress** — This project is under active development. Hardware interfaces are functional but the full BSA test suite integration is incomplete.

An open-source FPGA-based PCIe endpoint for [ARM BSA](https://developer.arm.com/documentation/den0094/latest) compliance testing. Plug it into an ARM64 system, run the [BSA ACS](https://github.com/ARM-software/sysarch-acs) test suite, and validate your SMMU/IOMMU, cache coherency, and interrupt handling.

## Architecture

The Arm BSA PCIe Exerciser is a PCIe endpoint designed for validation of an Arm BSA/SBSA compliant system.  The BSA/SBSA Architecture Compliance Suite, ACS, uses the exerciser to provide external stimulus and generate events that validate the PCIe Root Complex implementation on the test platform.

The Exerciser feature set is described in the **sysarch-acs** documentation [here](https://github.com/ARM-software/sysarch-acs/blob/main/docs/pcie/Exerciser.md).

The following block diagram shows the main functional blocks that make up the BSA PCIe Exerciser implemented in this repo.  Note that the USB interface is **not** an Arm BSA ACS requirement and provided for debug/monitoring only.

```
                      ┌───────────────────────────────────────────────────────────┐
                      │                     PCIe Squirrel Board                   │
                      │                                                           │
                      │  ┌───────────┐      ┌──────────────────────────────────┐  │
                      │  │           │      │       BSA Exerciser Core         │  │
                      │  │  Xilinx   │      │                                  │  │
  ARM64 System        │  │  PCIe     │      │  ┌───────┐ ┌───────┐ ┌────────┐  │  │
  Under Test ─────────┼──┤  PHY      │◄────►│  │ BAR0  │ │  DMA  │ │ MSI-X  │  │  │
  (PCIe x1)           │  │           │      │  │ Regs  │ │Engine │ │  INTx  │  │  │
                      │  │  Gen2 x1  │      │  └───────┘ └───────┘ └────────┘  │  │
                      │  │           │      │                                  │  │
                      │  └───────────┘      │  ┌───────┐ ┌───────┐ ┌────────┐  │  │
                      │                     │  │ PASID │ │  ATS  │ │Monitor │  │  │
                      │                     │  │Prefix │ │ Cache │ │ FIFO   │  │  │
                      │                     │  └───────┘ └───────┘ └───┬────┘  │  │
                      │                     └──────────────────────────┼───────┘  │
                      │                                                │          │
                      │  ┌───────────┐       Transaction capture       │          │
  Host PC ────────────┼──┤  FT601    │◄────────────────────────────────┘          │
  (monitoring/debug)  │  │  USB 3.0  │                                            │
  (USB 3.0)           │  └───────────┘                                            │
                      │                                                           │
                      └───────────────────────────────────────────────────────────┘

```

## Features

- **DMA Engine** — Generate memory read/write TLPs with configurable No-Snoop and PASID attributes
- **MSI-X Interrupts** — MSI-X vector table and legacy INTx support for interrupt testing
- **PASID Support** — TLP prefix injection for process address space isolation testing
- **ATS Cache** — Address Translation Services with translation request/completion handling
- **Transaction Monitor** — Capture inbound TLPs and stream via USB 3.0 for host-side analysis

## Supported Hardware

| Board | FPGA | PCIe | USB Monitor | Status |
|-------|------|------|-------------|--------|
| **Squirrel** (PCIe Screamer) | Artix-7 XC7A35T | Gen2 x1 | FT601 USB 3.0 | Primary target |
| SPEC-A7 | Artix-7 XC7A50T | Gen2 x1 | Ethernet | Supported |

The Squirrel (and similar PCILeech-derived boards like the CaptainDMA 4.1th) offers an excellent price/performance ratio for BSA testing. The FT601 USB interface enables real-time transaction monitoring without consuming PCIe bandwidth.

### LambdaConcept PCIe Squirrel

The Squirrel is a low-profile form factor board designed to be used with the [PCILeech](https://github.com/ufrisk/pcileech) DMA attack software.  The PCILeech software and gateware are designed to be used for system security analysis and testing but the board provides a low cost solution to BSA testing.

![Squirrel](https://shop.lambdaconcept.com/168-large_default/screamer-pcie-squirrel.jpg)

The board is available directly from LambdaConcept on their [webshop](https://shop.lambdaconcept.com/home/50-screamer-pcie-squirrel.html) for €159.00.

### CaptainDMA PCIe 4.1th

CaptainDMA produce a wide variety of PCILeech supported boards and the 4.1th seems to be a direct replacement for the LambdaConcept Squirrel.  At the time of writing, the 4.1th is available from the CaptainDMA webshop for $99 [here](https://captaindma.com/product/captain-dma-4-1th/).

![CaptainDMA](https://captaindma.com/wp-content/uploads/2023/09/captain-4.1.401-1.png.webp)

### SPEC A7

The SPEC A7 is a low cost White Rabbit node developed by the same teams behind the [Sinara](https://github.com/sinara-hw/meta/wiki) open-source hardware ecosystem used within ARTIQ.  The board is designed to be a low cost alternative to the main Ultrascale system boards and is available from TechnoSystem [here](https://sinara.technosystem.pl/modules/wr-node-spec-a7/).

![Spec_A7](https://sinara.technosystem.pl/app/uploads/2025/06/TOP-Spec-A7-PCIe-Module-1024x576.png)

The board is included here as it was the source of the LiteX PCIe SoC used as the baseline for this project.  You can find the original source code [here](https://github.com/enjoy-digital/litex_wr_nic).

While the board is supported, the LamdaConcept Squirrel offers a much more attractive price point and is the recommended board.

## Quick Start

### 1. Get the Hardware

You'll need a PCIe card such as the Squirrel or  and an ARM64 system with a free PCIe slot and SMMU enabled.

### 2. Program the FPGA

Download a pre-built bitstream from [Releases](https://github.com/sjalloq/bsa-pcie-exerciser/releases), or build from source:

```bash
git clone https://github.com/sjalloq/bsa-pcie-exerciser.git
cd bsa-pcie-exerciser
source sourceme
bsa-pcie-exerciser build --platform squirrel
```

Program via JTAG:

```bash
bsa-pcie-exerciser load --platform squirrel
```

### 3. Install in Target System

1. Power off the ARM64 system under test
2. Insert the programmed Squirrel into a PCIe slot
3. Connect USB cable to monitoring host (optional)
4. Power on and boot

### 4. Verify Enumeration

```bash
# Should show the BSA Exerciser device
lspci -d 13b5:00ed -v
```

Expected output shows:
- **BAR0**: Control/status registers
- **BAR1**: DMA buffer
- **BAR2/5**: MSI-X table and PBA

### 5. Run BSA Tests

```bash
# Using BSA ACS Linux application
cd /path/to/bsa-acs
./bsa -e          # Run exerciser tests
```

See the [BSA ACS documentation](https://github.com/ARM-software/sysarch-acs) for full test execution details.

## Transaction Monitoring

The Squirrel's USB interface provides real-time visibility into PCIe transactions:

```bash
# On the monitoring host PC
pip install bsa-monitor
bsa-monitor capture --live
```

This streams all inbound TLPs with full header details (address, attributes, byte enables) — essential for debugging SMMU translation failures.

## Documentation

Full documentation is available in the [docs/](docs/) directory:

- [Architecture Overview](docs/source/architecture.rst)
- [Register Map](docs/source/registers.rst)  
- [Building from Source](docs/source/building.rst)
- [USB Monitor Protocol](docs/source/usb_monitor.rst)

## Project Status

| Component | Status |
|-----------|--------|
| Multi-BAR routing | ✅ Complete |
| DMA engine | ✅ Complete |
| MSI-X (2048 vectors) | ✅ Complete |
| PASID prefix injection | ✅ Complete |
| ATS cache | ✅ Complete |
| Transaction monitor | ✅ Complete |
| USB streaming | 🔄 In progress |
| BSA ACS integration | 🔄 In progress |

## Related Projects

- [ARM BSA ACS](https://github.com/ARM-software/sysarch-acs) — Compliance test suite
- [LiteX](https://github.com/enjoy-digital/litex) — SoC builder framework
- [LitePCIe](https://github.com/enjoy-digital/litepcie) — PCIe core (using [forked version](https://github.com/sjalloq/litepcie/tree/feature/tlp-attributes) with TLP attribute support)
- [LiteX WR NIC](https://github.com/enjoy-digital/litex_wr_nic) - PCIe White Rabbit NIC

## License

BSD 3-Clause License. See [LICENSE](LICENSE) for details.
