# Integration Tests

Integration tests for the BSA PCIe Exerciser using cocotb and Icarus Verilog.

## Testbench Architecture

The integration testbench (`tb_integration.py`) instantiates the **real** `BSAExerciserSoC` with stub components for the PHY and platform. This allows testing the complete TLP flow through the system:

```
                    Integration Testbench
    ┌────────────────────────────────────────────────────┐
    │                                                    │
    │   ┌─────────┐      ┌──────────────────────────┐    │
    │   │         │      │     BSAExerciserSoC      │    │
    │   │ cocotb  │ TLP  │  ┌────────────────────┐  │    │
    │   │  BFM    │─────►│  │   Depacketizer     │  │    │
    │   │         │      │  └─────────┬──────────┘  │    │
    │   │         │      │            │             │    │
    │   │         │      │  ┌─────────▼──────────┐  │    │
    │   │         │      │  │   BAR Dispatcher   │  │    │
    │   │         │      │  └─────────┬──────────┘  │    │
    │   │         │      │            │             │    │
    │   │         │      │  ┌─────────▼──────────┐  │    │
    │   │         │◄─────│  │   BSA Registers    │  │    │
    │   │         │ Cpl  │  │   DMA Engine       │  │    │
    │   │         │      │  │   MSI-X / INTx     │  │    │
    │   └─────────┘      │  └────────────────────┘  │    │
    │                    └──────────────────────────┘    │
    └────────────────────────────────────────────────────┘
```

### Exposed Signals

The testbench exposes PHY-level signals for TLP injection and capture:

| Signal          | Direction | Description                        |
|-----------------|-----------|------------------------------------|
| `sys_clk`       | input     | System clock (125 MHz / 8ns)       |
| `sys_rst`       | input     | System reset                       |
| `pcie_clk`      | input     | PCIe clock (100 MHz / 10ns)        |
| `pcie_rst`      | input     | PCIe reset                         |
| `phy_rx_*`      | input     | RX path: inject TLPs into DUT      |
| `phy_tx_*`      | output    | TX path: capture TLPs from DUT     |
| `intx_asserted` | output    | Legacy interrupt state             |

### Clock Domains

The design uses two clock domains:
- **sys**: Main system clock at 125 MHz
- **pcie**: PCIe interface clock at 100 MHz

Both clocks must be started in tests:
```python
cocotb.start_soon(Clock(dut.sys_clk, 8, unit="ns").start())
cocotb.start_soon(Clock(dut.pcie_clk, 10, unit="ns").start())
```

## Running Tests

```bash
# Run all tests
make sim

# Run specific test
make sim TESTCASE=test_bar0_id_register_read

# Run with specific random seed (for reproducibility)
RANDOM_SEED=12345 make sim TESTCASE=test_random_bar0

# Run with more iterations
N_TRANSACTIONS=500 make sim TESTCASE=test_random_bar0

# Clean build artifacts
make clean
```

## Test Files

| File                | Description                                  |
|---------------------|----------------------------------------------|
| `test_registers.py` | Directed tests for BAR0 register access      |
| `test_msix.py`      | MSI-X interrupt tests                        |
| `test_intx.py`      | Legacy INTx interrupt tests                  |
| `test_random.py`    | Randomized tests with coverage collection    |

## Randomized Testing

The `test_random.py` file provides constrained-random testing to exercise the TLP parameter space more thoroughly than directed tests.

### Available Tests

| Test                               | Description                               |
|------------------------------------|-------------------------------------------|
| `test_random_bar0`                 | Random BAR0 register R/W with varied tags |
| `test_random_bar0_rapid`           | Rapid-fire back-to-back transactions      |
| `test_random_bar1_rw`              | Random BAR1 buffer writes + verification  |
| `test_random_attributes`           | DMA attribute testing (No-Snoop, AT)      |
| `test_backpressure_stress`         | TX back-pressure stress test              |
| `test_tag_range`                   | Full tag range (0-255) verification       |
| `test_random_msix_table`           | Random MSI-X table (BAR2) R/W             |
| `test_dma_buffer_boundaries`       | Buffer boundary edge cases                |
| `test_completion_timeout_recovery` | System recovery after timeouts            |

### Convenience Makefile Targets

```bash
# Run all randomized tests with time-based seed
make random

# Run with specific seed for reproduction
make random-seed SEED=12345

# Run stress tests only
make stress

# View coverage report
make coverage-report

# Reset coverage and run fresh
make coverage-reset
```

### Reproducibility

All randomized tests use a seed that can be controlled via environment variable:

```bash
# Default seed is 42
make sim TESTCASE=test_random_bar0

# Use specific seed for reproduction
RANDOM_SEED=12345 make sim TESTCASE=test_random_bar0

# The test logs the seed for reproduction:
# "To reproduce: RANDOM_SEED=12345 make sim TESTCASE=test_random_bar0"
```

## Coverage Collection

Randomized tests collect functional coverage to track which parameter combinations have been exercised.

### Coverage Files

| File                   | Description                                    |
|------------------------|------------------------------------------------|
| `coverage_random.json` | Raw coverage data (for merging across runs)    |
| `coverage_random.txt`  | Human-readable coverage report                 |

### Accumulating Coverage

By default, coverage accumulates across runs:

```bash
# Run multiple tests - coverage accumulates
make sim TESTCASE=test_random_bar0
make sim TESTCASE=test_tag_range
make sim TESTCASE=test_random_attributes

# Run with different seeds - all contribute to coverage
RANDOM_SEED=100 make sim TESTCASE=test_random_bar0
RANDOM_SEED=200 make sim TESTCASE=test_random_bar0
RANDOM_SEED=300 make sim TESTCASE=test_random_bar0
```

### Resetting Coverage

To start fresh instead of accumulating:

```bash
# Delete existing coverage and start fresh
RESET_COVERAGE=1 make sim TESTCASE=test_random_bar0

# Or manually delete the file
rm coverage_random.json
```

### Coverage Report

The coverage report (`coverage_random.txt`) shows:
- Total samples and unique bins hit
- Per-coverpoint breakdown with distribution
- Cross-coverage combinations

Example output:
```
============================================================
Coverage Report: BSA_PCIe
============================================================
Total samples: 1250, Unique bins: 47

bar0_offset_read:
  Bins: 13, Samples: 312
    0x48: 45 (14.4%)
    0x08: 38 (12.2%)
    ...

mrd_tag_range:
  Bins: 3, Samples: 312
    0-31: 142 (45.5%)
    32-127: 98 (31.4%)
    128-255: 72 (23.1%)

Cross Coverage:
  dma_ns_x_at: 6 combinations
============================================================
```

## Common BFMs

Tests use shared BFMs from `tests/common/`:

| Module           | Description                                         |
|------------------|-----------------------------------------------------|
| `pcie_bfm.py`    | PCIe Bus Functional Model for TLP injection/capture |
| `tlp_builder.py` | TLP construction and parsing helpers                |
| `randomizer.py`  | Constrained-random TLP parameter generator          |
| `coverage.py`    | Functional coverage collection                      |

### PCIeBFM Usage

```python
from tbench.common.pcie_bfm import PCIeBFM
from tbench.common.tlp_builder import TLPBuilder

bfm = PCIeBFM(dut)

# Inject a memory write TLP to BAR0
data = (0x12345678).to_bytes(4, 'little')
beats = TLPBuilder.memory_write_32(address=0x08, data_bytes=data)
await bfm.inject_tlp(beats, bar_hit=0b000001)

# Inject a memory read and capture completion
beats = TLPBuilder.memory_read_32(address=0x48, length_dw=1, tag=5)
await bfm.inject_tlp(beats, bar_hit=0b000001)
cpl = await bfm.capture_tlp(timeout_cycles=200)
```

## Register Map Reference

BAR0 register offsets (from `core/bsa_registers.py`):

| Offset | Name            | Access | Description                        |
|--------|-----------------|--------|------------------------------------|
| 0x00   | MSICTL          | R/W    | MSI-X control                      |
| 0x04   | INTXCTL         | R/W    | Legacy interrupt control           |
| 0x08   | DMACTL          | R/W    | DMA control (trigger auto-clears)  |
| 0x0C   | DMA_OFFSET      | R/W    | DMA buffer offset                  |
| 0x10   | DMA_BUS_ADDR_LO | R/W    | DMA host address low               |
| 0x14   | DMA_BUS_ADDR_HI | R/W    | DMA host address high              |
| 0x18   | DMA_LEN         | R/W    | DMA transfer length                |
| 0x1C   | DMASTATUS       | R/W    | DMA status                         |
| 0x20   | PASID_VAL       | R/W    | PASID value                        |
| 0x24   | ATSCTL          | R/W    | ATS control                        |
| 0x28   | ATS_ADDR_LO     | RO     | ATS translated addr low            |
| 0x2C   | ATS_ADDR_HI     | RO     | ATS translated addr high           |
| 0x30   | ATS_RANGE_SIZE  | RO     | ATS range size                     |
| 0x38   | ATS_PERM        | RO     | ATS permissions                    |
| 0x3C   | RID_CTL         | R/W    | Requester ID override              |
| 0x40   | TXN_TRACE       | RO     | Transaction trace FIFO             |
| 0x44   | TXN_CTRL        | R/W    | Transaction monitor control        |
| 0x48   | ID              | RO     | Device/Vendor ID (0xED0113B5)      |
