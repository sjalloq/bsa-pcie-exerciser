# BSA PCIe Exerciser

An FPGA-based PCIe endpoint for ARM Base System Architecture (BSA) compliance testing.

## Overview

The BSA PCIe Exerciser is a hardware test device that plugs into a PCIe slot on an ARM64 system under test. It implements the [ARM BSA Exerciser specification](https://github.com/ARM-software/sysarch-acs/blob/main/docs/pcie/Exerciser.md) and works with the [ARM BSA ACS](https://github.com/ARM-software/sysarch-acs) (Architecture Compliance Suite) to validate:

- SMMU/IOMMU functionality via configurable TLP attributes
- Cache coherency through No-Snoop attribute control
- Address translation using ATS and PASID
- MSI-X interrupt handling (2048 vectors)

The exerciser is software-controlled: BSA ACS tests write to device registers to trigger specific PCIe transactions, then verify the system handled them correctly.

## Supported Hardware

### FPGA Platforms

| Board | FPGA | PCIe | Status |
|-------|------|------|--------|
| SPEC-A7 | Artix-7 XC7A50T | Gen2 x1 | Supported |
| CaptainDMA / Squirrel | Artix-7 XC7A35T | Gen2 x1 | Planned |

### Host System Requirements

- **Architecture**: AArch64 (ARM64)
- **PCIe slot**: x1 or wider, Gen2 capable
- **IOMMU**: ARM SMMU recommended for full test coverage
- **OS**: Linux with BSA ACS kernel module support

## Building

### Prerequisites

- Xilinx Vivado 2023.x or later
- Python 3.10+
- OpenFPGALoader (for JTAG programming)

### Build Steps

```bash
# Clone repository
git clone https://github.com/your-org/bsa-pcie-exerciser.git
cd bsa-pcie-exerciser

# Set up environment
source sourceme

# Build bitstream for your target board
make build PLATFORM=spec_a7
```

Build outputs are placed in `build/<platform>/gateware/`.

## Installation

### Programming the FPGA

With the board connected via JTAG:

```bash
# Program volatile (lost on power cycle)
bsa-pcie-exerciser --load

# Program flash (persistent)
bsa-pcie-exerciser --flash
```

### Physical Installation

1. Power off the system under test
2. Install the programmed FPGA board in a PCIe slot
3. Power on and boot to Linux

### Verifying Enumeration

The exerciser should appear as a PCIe device:

```bash
lspci -d 1234:5678 -v
```

You should see the device with multiple BARs:
- BAR0: Control registers
- BAR2: DMA buffer space

## Running BSA Tests

The exerciser integrates with ARM's BSA ACS test suite. Once the device is installed and enumerated:

1. Build and load the BSA ACS UEFI application or Linux module
2. The ACS automatically detects exerciser devices
3. Run the PCIe exerciser test groups

Refer to the [BSA ACS documentation](https://github.com/ARM-software/sysarch-acs) for detailed test execution instructions.

## Documentation

- [Project Documentation](docs/source/) — Architecture, register maps, and implementation details
- [ARM BSA Exerciser Specification](https://github.com/ARM-software/sysarch-acs/blob/main/docs/pcie/Exerciser.md) — Official specification this project implements
- [ARM BSA Specification](https://developer.arm.com/documentation/den0094/latest) — Base System Architecture requirements

## Related Projects

- [ARM BSA ACS](https://github.com/ARM-software/sysarch-acs) — The compliance test suite that uses this exerciser
- [LiteX](https://github.com/enjoy-digital/litex) — SoC builder framework
- [LitePCIe](https://github.com/enjoy-digital/litepcie) — PCIe core for LiteX

## License

BSD 3-Clause License. See [LICENSE](LICENSE) for details.
