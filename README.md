# BSA PCIe Exerciser

ARM BSA/SBSA PCIe Exerciser implementation using LiteX/LitePCIe framework.

## Overview

This exerciser implements the ARM Base System Architecture (BSA) PCIe exerciser
specification for compliance testing. It enables validation of:

- SMMU/IOMMU functionality via configurable TLP attributes
- Cache coherency via No-Snoop attribute control
- Address translation via PASID and ATS support
- Interrupt handling via MSI/MSI-X generation
- Transaction monitoring for compliance verification

## Repository Structure

```
bsa-exerciser/
├── README.md
├── requirements.txt              # Python dependencies
├── setup_env.sh                  # Environment setup script
│
├── bsa_exerciser/               # Core exerciser IP
│   ├── __init__.py
│   ├── dma.py                   # BSA DMA engine (register-triggered)
│   ├── regs.py                  # BAR0 register bank
│   ├── msi.py                   # MSI/MSI-X trigger logic
│   ├── monitor.py               # Transaction monitor
│   └── core.py                  # Top-level integration
│
├── targets/                      # Board-specific builds
│   └── spec_a7.py               # SPEC-A7 target
│
├── software/                     # Host-side tools
│   ├── test_exerciser.py        # Python test utility
│   └── linux/                   # Linux driver (if needed)
│
├── verify/                       # Verification
│   ├── Makefile
│   ├── generate_verilog.py
│   └── tb/
│       ├── bfm/
│       └── test_*.py
│
└── docs/                         # Documentation
    ├── IMPLEMENTATION_PLAN.md
    ├── REGISTER_MAP.md
    └── VERIFICATION.md
```

## Dependencies

- **LitePCIe fork** with attribute passthrough support
- LiteX framework
- Migen
- Python 3.8+

## Quick Start

```bash
# 1. Clone this repo
git clone <this-repo>
cd bsa-exerciser

# 2. Set up Python environment
./setup_env.sh

# 3. Clone and patch LitePCIe fork
git clone https://github.com/<your-org>/litepcie.git deps/litepcie
# Apply patches per docs/LITEPCIE_PATCHES.md

# 4. Build for SPEC-A7
python targets/spec_a7.py --build

# 5. Run verification
cd verify && make test
```

## Implementation Status

| Feature | Status | Phase |
|---------|--------|-------|
| BAR0 Registers | 🔲 TODO | 1 |
| Basic DMA (read/write) | 🔲 TODO | 1 |
| No-Snoop attribute | 🔲 TODO | 1 |
| MSI-X generation | 🔲 TODO | 1 |
| Transaction monitor | 🔲 TODO | 1 |
| PASID TLP prefix | 🔲 TODO | 2 |
| ATS (AT field) | 🔲 TODO | 3 |
| ATS completions + ATC | 🔲 TODO | 3 |

## References

- [ARM BSA ACS](https://github.com/ARM-software/sysarch-acs)
- [Exerciser Spec](https://github.com/ARM-software/sysarch-acs/blob/main/docs/pcie/Exerciser.md)
- [LitePCIe](https://github.com/enjoy-digital/litepcie)
