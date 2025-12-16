# Getting Started

## Prerequisites

1. **Python 3.8+** with venv support
2. **Vivado** (for SPEC-A7 builds) or simulation-only setup
3. **Verilator** (for simulation): `apt install verilator`
4. **GTKWave** (for waveforms): `apt install gtkwave`

## Step 1: Set Up This Repository

```bash
# Clone your bare repo
git clone <your-repo-url> bsa-exerciser
cd bsa-exerciser

# Create Python venv
python3 -m venv venv
source venv/bin/activate

# Install dependencies (excluding litepcie for now)
pip install migen litex cocotb pytest
```

## Step 2: Set Up LitePCIe Fork

You need a patched LitePCIe with attribute passthrough support.

```bash
# Clone your LitePCIe fork
mkdir -p deps
git clone https://github.com/<your-org>/litepcie.git deps/litepcie
cd deps/litepcie

# Create branch for BSA support
git checkout -b attr-passthrough

# Apply the minimal patches (see below)
# Then return to main repo
cd ../..

# Install your fork in editable mode
pip install -e deps/litepcie
```

### LitePCIe Patches Required

**Patch 1: `litepcie/common.py`**

Find `request_layout()` function and add two fields:

```python
def request_layout(data_width, address_width=32):
    layout = [
        ("req_id",          16),
        ("we",               1),
        ("adr",  address_width),
        ("len",             10),
        ("tag",              8),
        ("dat",     data_width),
        ("channel",          8),
        ("user_id",          8),
        # === ADD THESE TWO LINES ===
        ("attr",             2),  # TLP Attributes: [0]=NS, [1]=RO
        ("at",               2),  # Address Type for ATS
    ]
    return EndpointDescription(layout)
```

**Patch 2: `litepcie/tlp/packetizer.py`**

Find the line (around line 115-120 in the "REQUEST" section):
```python
tlp_req.attr.eq(0),
```

Change to:
```python
tlp_req.attr.eq(req_sink.attr),
```

**Commit your patches:**
```bash
cd deps/litepcie
git add -A
git commit -m "Add TLP attribute passthrough for BSA exerciser support"
git push origin attr-passthrough
```

## Step 3: Verify Setup

```bash
# Activate venv
source venv/bin/activate

# Test imports
python -c "from bsa_exerciser import BSAExerciserDMA; print('OK')"
python -c "from litepcie.core.endpoint import LitePCIeEndpoint; print('LitePCIe OK')"
```

## Step 4: Run Simulation Tests (No Hardware Needed)

```bash
cd verify

# Generate Verilog from Migen (once DMA core is complete)
python generate_verilog.py --all

# Run cocotb tests
make test_dma
```

## Step 5: Build for SPEC-A7 (Requires Vivado)

```bash
# Set up Vivado environment
source /path/to/Vivado/2023.1/settings64.sh

# Build
python targets/spec_a7.py --build
```

## Development Workflow

### Recommended Order

1. **LitePCIe fork** - Apply patches, verify builds
2. **BSA DMA module** - Complete `bsa_exerciser/dma.py`
3. **Simulation tests** - Add cocotb tests for DMA
4. **x86 testing** - Build for SPEC-A7, test in x86 PC
5. **Phase 2** - Add PASID support
6. **ARM HAPS** - Final BSA ACS validation

### Quick Test Cycle

```bash
# Edit code
vim bsa_exerciser/dma.py

# Regenerate Verilog
cd verify && python generate_verilog.py --module dma

# Run tests
make test_dma

# View waveforms if needed
make waves
```

## Repository Structure After Setup

```
bsa-exerciser/
├── venv/                        # Python virtual environment
├── deps/
│   └── litepcie/               # Your LitePCIe fork (patched)
├── bsa_exerciser/
│   ├── __init__.py
│   └── dma.py                  # ← Start here
├── verify/
│   └── tb/
│       └── test_dma.py         # ← Then add tests
└── targets/
    └── spec_a7.py              # ← Finally, board target
```

## Next Steps

1. Complete the `BSAExerciserDMA` module in `bsa_exerciser/dma.py`
2. Add the register bank module (`bsa_exerciser/regs.py`)
3. Create the top-level integration (`bsa_exerciser/core.py`)
4. Write cocotb tests
5. Build and test on x86

See `docs/IMPLEMENTATION_PLAN.md` for detailed phasing.
