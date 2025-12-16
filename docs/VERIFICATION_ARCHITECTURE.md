# Verification Architecture for BSA Exerciser

## The Migen Verification Problem

You've identified the core issues:

1. **Baked-in Xilinx primitives** - MMCM, BUFG, GTP, etc. make simulation impossible
2. **Module instance passing** - `def __init__(self, pcie_phy)` where you pass a full 
   instantiated module breaks Verilog generation at that hierarchy level
3. **No clear Core/Wrapper separation** - Everything gets tangled together
4. **LiteX's `run_simulation()`** - Limited, slow, no coverage, poor debug

## Our Verification Strategy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        VERIFICATION ARCHITECTURE                            │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│  LEVEL 1: Unit Tests (cocotb + Verilator)                                  │
│  =========================================                                  │
│  - Test individual *Core modules in isolation                              │
│  - Mock all external interfaces with cocotb BFMs                           │
│  - Fast iteration, full visibility                                         │
│                                                                             │
│      ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│      │ BSAExerciser    │     │ BSAExerciser    │     │ BSAExerciser    │   │
│      │ RegsCore        │     │ DMACore         │     │ ATSCore         │   │
│      │                 │     │                 │     │                 │   │
│      │ (Pure logic,    │     │ (Pure logic,    │     │ (Pure logic,    │   │
│      │  no primitives) │     │  no primitives) │     │  no primitives) │   │
│      └────────┬────────┘     └────────┬────────┘     └────────┬────────┘   │
│               │                       │                       │            │
│               ▼                       ▼                       ▼            │
│      ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐   │
│      │ cocotb          │     │ cocotb          │     │ cocotb          │   │
│      │ Wishbone BFM    │     │ TLP Stream BFM  │     │ ATS BFM         │   │
│      └─────────────────┘     └─────────────────┘     └─────────────────┘   │
│                                                                             │
│  LEVEL 2: Integration Tests (cocotb + Verilator)                           │
│  ================================================                           │
│  - Test BSAExerciserCore (all submodules together)                         │
│  - Mock LitePCIe at TLP interface boundary                                 │
│  - Uses cocotbext-pcie for realistic PCIe modeling                         │
│                                                                             │
│      ┌─────────────────────────────────────────────────────────────────┐   │
│      │                    BSAExerciserCore                              │   │
│      │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │   │
│      │  │ RegsCore │  │ DMACore  │  │ ATSCore  │  │ TxnMonCore   │    │   │
│      │  └──────────┘  └──────────┘  └──────────┘  └──────────────┘    │   │
│      └──────────────────────────┬──────────────────────────────────────┘   │
│                                 │                                          │
│                    TLP Request/Completion Streams                          │
│                                 │                                          │
│      ┌──────────────────────────▼──────────────────────────────────────┐   │
│      │                    cocotbext-pcie                                │   │
│      │              (Root Complex + IOMMU model)                        │   │
│      └─────────────────────────────────────────────────────────────────┘   │
│                                                                             │
│  LEVEL 3: System Tests (LiteX Sim or FPGA)                                 │
│  ==========================================                                 │
│  - Full SoC with LitePCIe PHY                                              │
│  - Either Verilator (slow) or actual FPGA                                  │
│  - Smoke tests only - detailed testing done at Level 1/2                   │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Module Structure: Core vs Wrapper Pattern

Every module follows this pattern:

```python
# bsa_exerciser/dma.py

class BSAExerciserDMACore(LiteXModule):
    """
    CORE: Pure synthesizable logic, no vendor primitives.
    
    This is what we verify with cocotb.
    All interfaces are simple signals or Records - no module instances.
    """
    def __init__(self, data_width=64):
        # =====================================================================
        # INTERFACE: Simple signals only, no module instances!
        # =====================================================================
        
        # Control (directly driveable from testbench)
        self.start       = Signal()
        self.direction   = Signal()
        self.bus_addr    = Signal(64)
        # ... etc ...
        
        # TLP interface (directly driveable from testbench)
        self.tlp_tx = Record([
            ("valid", 1),
            ("ready", 1),
            ("data",  data_width),
            ("last",  1),
        ])
        self.tlp_rx = Record([
            ("valid", 1),
            ("ready", 1),
            ("data",  data_width),
            ("last",  1),
        ])
        
        # =====================================================================
        # IMPLEMENTATION: Pure logic
        # =====================================================================
        
        # ... state machines, combinational logic, BRAMs ...
        # NO: MMCM, BUFG, IDELAYE2, GTP, etc.


class BSAExerciserDMA(LiteXModule):
    """
    WRAPPER: Instantiates Core and connects to actual LitePCIe.
    
    This is NOT directly verified - we trust the Core tests
    and the LitePCIe tests separately.
    """
    def __init__(self, pcie_endpoint, data_width=64):
        # Instantiate the verified core
        self.core = BSAExerciserDMACore(data_width=data_width)
        
        # Connect to actual LitePCIe endpoint
        # This is where the "module instance passing" happens,
        # but it's isolated to the wrapper level
        self.comb += [
            # Connect core's TLP interface to LitePCIe
            pcie_endpoint.source.connect(self.core.tlp_rx),
            self.core.tlp_tx.connect(pcie_endpoint.sink),
        ]
```

## Verilog Generation Strategy

```python
# verify/generate_verilog.py

"""
Generate Verilog for all *Core modules.
Run this to update RTL before simulation.
"""

from migen import *
from migen.fhdl import verilog

import sys
sys.path.insert(0, '..')

from bsa_exerciser.dma import BSAExerciserDMACore
from bsa_exerciser.regs import BSAExerciserRegsCore
from bsa_exerciser.ats import BSAExerciserATSCore
from bsa_exerciser.core import BSAExerciserCore

def generate_module(module_class, name, **kwargs):
    """Generate Verilog for a single module."""
    
    # Create a minimal platform (no actual FPGA features)
    from migen.build.generic_platform import GenericPlatform
    
    # Instantiate the module
    module = module_class(**kwargs)
    
    # Convert to Verilog
    output = verilog.convert(
        module,
        name=name,
        ios=set()  # Will auto-detect IOs
    )
    
    # Write to file
    filename = f"rtl/{name}.v"
    with open(filename, 'w') as f:
        f.write(output)
    
    print(f"Generated {filename}")
    return filename


def main():
    import os
    os.makedirs("rtl", exist_ok=True)
    
    # Generate all core modules
    modules = [
        (BSAExerciserRegsCore,  "bsa_exerciser_regs_core",  {}),
        (BSAExerciserDMACore,   "bsa_exerciser_dma_core",   {"data_width": 64}),
        (BSAExerciserATSCore,   "bsa_exerciser_ats_core",   {"atc_entries": 16}),
        (BSAExerciserCore,      "bsa_exerciser_core",       {"local_mem_size": 8192}),
    ]
    
    for cls, name, kwargs in modules:
        try:
            generate_module(cls, name, **kwargs)
        except Exception as e:
            print(f"ERROR generating {name}: {e}")
            raise


if __name__ == "__main__":
    main()
```

## Makefile for Verification Flow

```makefile
# verify/Makefile

# Tools
PYTHON      ?= python3
VERILATOR   ?= verilator
COCOTB_CONFIG = cocotb-config

# Directories
RTL_DIR     = rtl
TB_DIR      = tb
SIM_DIR     = sim
WAVE_DIR    = waves

# Verilog sources (auto-generated from Migen)
VERILOG_SOURCES = \
    $(RTL_DIR)/bsa_exerciser_regs_core.v \
    $(RTL_DIR)/bsa_exerciser_dma_core.v \
    $(RTL_DIR)/bsa_exerciser_ats_core.v \
    $(RTL_DIR)/bsa_exerciser_core.v

# cocotb settings
export COCOTB_REDUCED_LOG_FMT = 1
export COCOTB_RESOLVE_X = ZEROS

# Default target
.PHONY: all
all: generate test

# Generate Verilog from Migen
.PHONY: generate
generate:
	$(PYTHON) generate_verilog.py

# Run all tests
.PHONY: test
test: test_regs test_dma test_ats test_integration

# Individual test targets
.PHONY: test_regs
test_regs: $(RTL_DIR)/bsa_exerciser_regs_core.v
	cd $(TB_DIR) && \
	MODULE=test_regs \
	TOPLEVEL=bsa_exerciser_regs_core \
	TOPLEVEL_LANG=verilog \
	VERILOG_SOURCES=../$(RTL_DIR)/bsa_exerciser_regs_core.v \
	SIM=verilator \
	$(PYTHON) -m pytest test_regs.py -v

.PHONY: test_dma
test_dma: $(RTL_DIR)/bsa_exerciser_dma_core.v
	cd $(TB_DIR) && \
	MODULE=test_dma \
	TOPLEVEL=bsa_exerciser_dma_core \
	TOPLEVEL_LANG=verilog \
	VERILOG_SOURCES=../$(RTL_DIR)/bsa_exerciser_dma_core.v \
	SIM=verilator \
	$(PYTHON) -m pytest test_dma.py -v

.PHONY: test_ats
test_ats: $(RTL_DIR)/bsa_exerciser_ats_core.v
	cd $(TB_DIR) && \
	MODULE=test_ats \
	TOPLEVEL=bsa_exerciser_ats_core \
	TOPLEVEL_LANG=verilog \
	VERILOG_SOURCES=../$(RTL_DIR)/bsa_exerciser_ats_core.v \
	SIM=verilator \
	$(PYTHON) -m pytest test_ats.py -v

.PHONY: test_integration
test_integration: $(RTL_DIR)/bsa_exerciser_core.v
	cd $(TB_DIR) && \
	MODULE=test_integration \
	TOPLEVEL=bsa_exerciser_core \
	TOPLEVEL_LANG=verilog \
	VERILOG_SOURCES=../$(RTL_DIR)/bsa_exerciser_core.v \
	SIM=verilator \
	$(PYTHON) -m pytest test_integration.py -v

# Waveform viewing
.PHONY: waves
waves:
	gtkwave $(WAVE_DIR)/dump.vcd &

# Clean
.PHONY: clean
clean:
	rm -rf $(RTL_DIR)/*.v
	rm -rf $(SIM_DIR)
	rm -rf $(WAVE_DIR)
	rm -rf __pycache__ $(TB_DIR)/__pycache__
	rm -rf .pytest_cache $(TB_DIR)/.pytest_cache
```

## cocotb Testbench Structure

### BFMs (Bus Functional Models)

```python
# verify/tb/bfm/wishbone.py

"""
Wishbone BFM for driving exerciser register interface.
"""

import cocotb
from cocotb.triggers import RisingEdge, ReadOnly
from cocotb.clock import Clock


class WishboneMaster:
    """
    Simple Wishbone master BFM.
    
    Drives the exerciser's BAR0 register interface.
    """
    def __init__(self, dut, prefix="wb_", clock=None):
        self.dut = dut
        self.clock = clock or dut.clk
        
        # Get signals with prefix
        self.cyc   = getattr(dut, f"{prefix}cyc")
        self.stb   = getattr(dut, f"{prefix}stb")
        self.we    = getattr(dut, f"{prefix}we")
        self.adr   = getattr(dut, f"{prefix}adr")
        self.dat_w = getattr(dut, f"{prefix}dat_w")
        self.dat_r = getattr(dut, f"{prefix}dat_r")
        self.ack   = getattr(dut, f"{prefix}ack")
        self.sel   = getattr(dut, f"{prefix}sel", None)
        
        # Initialize
        self.cyc.value = 0
        self.stb.value = 0
        self.we.value = 0
        
    async def write(self, address, data):
        """Perform a Wishbone write cycle."""
        await RisingEdge(self.clock)
        
        self.cyc.value = 1
        self.stb.value = 1
        self.we.value = 1
        self.adr.value = address
        self.dat_w.value = data
        if self.sel:
            self.sel.value = 0xF
        
        # Wait for ack
        while True:
            await RisingEdge(self.clock)
            if self.ack.value:
                break
        
        self.cyc.value = 0
        self.stb.value = 0
        self.we.value = 0
        
    async def read(self, address):
        """Perform a Wishbone read cycle."""
        await RisingEdge(self.clock)
        
        self.cyc.value = 1
        self.stb.value = 1
        self.we.value = 0
        self.adr.value = address
        if self.sel:
            self.sel.value = 0xF
        
        # Wait for ack
        while True:
            await RisingEdge(self.clock)
            if self.ack.value:
                break
        
        await ReadOnly()
        data = self.dat_r.value.integer
        
        self.cyc.value = 0
        self.stb.value = 0
        
        return data
```

```python
# verify/tb/bfm/tlp_stream.py

"""
TLP Stream BFM for driving/monitoring exerciser PCIe interface.

This mocks the LitePCIe TLP layer at the stream interface level.
"""

import cocotb
from cocotb.triggers import RisingEdge, Timer, First
from cocotb.queue import Queue
from collections import namedtuple

TLPPacket = namedtuple('TLPPacket', [
    'fmt', 'type', 'tc', 'attr', 
    'length', 'requester_id', 'tag',
    'address', 'data',
    'pasid_valid', 'pasid_value', 'pasid_pmr', 'pasid_exe',
    'address_type'
])


class TLPSource:
    """
    Generates TLP completions toward the exerciser.
    (Simulates root complex responses to exerciser requests)
    """
    def __init__(self, dut, prefix="tlp_rx_", clock=None):
        self.dut = dut
        self.clock = clock or dut.clk
        
        self.valid = getattr(dut, f"{prefix}valid")
        self.ready = getattr(dut, f"{prefix}ready")
        self.data  = getattr(dut, f"{prefix}data")
        self.last  = getattr(dut, f"{prefix}last")
        
        self.queue = Queue()
        
        # Initialize
        self.valid.value = 0
        self.data.value = 0
        self.last.value = 0
        
        # Start driver coroutine
        cocotb.start_soon(self._driver())
        
    async def _driver(self):
        """Background coroutine that sends queued TLPs."""
        while True:
            packet = await self.queue.get()
            await self._send_packet(packet)
    
    async def _send_packet(self, packet):
        """Send a single TLP packet."""
        # Convert packet to DWs
        dws = self._packet_to_dws(packet)
        
        for i, dw in enumerate(dws):
            await RisingEdge(self.clock)
            self.valid.value = 1
            self.data.value = dw
            self.last.value = (i == len(dws) - 1)
            
            # Wait for ready
            while True:
                await RisingEdge(self.clock)
                if self.ready.value:
                    break
        
        self.valid.value = 0
        self.last.value = 0
    
    def _packet_to_dws(self, packet):
        """Convert TLPPacket to list of DWs."""
        dws = []
        
        # DW0: Fmt, Type, TC, Attr, Length
        dw0 = (packet.fmt << 29) | (packet.type << 24) | \
              (packet.tc << 20) | (packet.attr << 12) | packet.length
        dws.append(dw0)
        
        # DW1: Requester ID, Tag, BE
        dw1 = (packet.requester_id << 16) | (packet.tag << 8) | 0xFF
        dws.append(dw1)
        
        # DW2/3: Address
        if packet.address > 0xFFFFFFFF:
            dws.append((packet.address >> 32) & 0xFFFFFFFF)
            dws.append((packet.address & 0xFFFFFFFC) | packet.address_type)
        else:
            dws.append((packet.address & 0xFFFFFFFC) | packet.address_type)
        
        # Data DWs
        if packet.data:
            for i in range(0, len(packet.data), 4):
                dw = int.from_bytes(packet.data[i:i+4], 'little')
                dws.append(dw)
        
        return dws
    
    async def send_completion(self, requester_id, tag, data, status=0):
        """Send a completion TLP."""
        packet = TLPPacket(
            fmt=0b010,  # 3DW with data
            type=0b01010,  # Completion
            tc=0,
            attr=0,
            length=len(data) // 4,
            requester_id=requester_id,
            tag=tag,
            address=0,
            data=data,
            pasid_valid=False,
            pasid_value=0,
            pasid_pmr=False,
            pasid_exe=False,
            address_type=0
        )
        await self.queue.put(packet)


class TLPSink:
    """
    Captures TLPs from the exerciser.
    (Monitors exerciser requests to root complex)
    """
    def __init__(self, dut, prefix="tlp_tx_", clock=None):
        self.dut = dut
        self.clock = clock or dut.clk
        
        self.valid = getattr(dut, f"{prefix}valid")
        self.ready = getattr(dut, f"{prefix}ready")
        self.data  = getattr(dut, f"{prefix}data")
        self.last  = getattr(dut, f"{prefix}last")
        
        self.received = Queue()
        
        # Always ready by default
        self.ready.value = 1
        
        # Start monitor coroutine
        cocotb.start_soon(self._monitor())
    
    async def _monitor(self):
        """Background coroutine that captures TLPs."""
        current_dws = []
        
        while True:
            await RisingEdge(self.clock)
            
            if self.valid.value and self.ready.value:
                current_dws.append(self.data.value.integer)
                
                if self.last.value:
                    packet = self._dws_to_packet(current_dws)
                    await self.received.put(packet)
                    current_dws = []
    
    def _dws_to_packet(self, dws):
        """Convert list of DWs to TLPPacket."""
        # Check for PASID prefix
        pasid_valid = False
        pasid_value = 0
        pasid_pmr = False
        pasid_exe = False
        
        idx = 0
        if len(dws) > 0 and ((dws[0] >> 24) & 0xFF) == 0x21:
            # PASID prefix present
            pasid_valid = True
            pasid_value = dws[0] & 0xFFFFF
            pasid_pmr = bool(dws[0] & (1 << 21))
            pasid_exe = bool(dws[0] & (1 << 20))
            idx = 1
        
        # Parse header
        dw0 = dws[idx]
        fmt = (dw0 >> 29) & 0x7
        tlp_type = (dw0 >> 24) & 0x1F
        tc = (dw0 >> 20) & 0x7
        attr = (dw0 >> 12) & 0x7
        length = dw0 & 0x3FF
        
        dw1 = dws[idx + 1]
        requester_id = (dw1 >> 16) & 0xFFFF
        tag = (dw1 >> 8) & 0xFF
        
        # Address
        is_64bit = fmt & 0x1
        if is_64bit:
            addr_hi = dws[idx + 2]
            addr_lo = dws[idx + 3]
            address = (addr_hi << 32) | (addr_lo & 0xFFFFFFFC)
            address_type = addr_lo & 0x3
            data_start = idx + 4
        else:
            addr_lo = dws[idx + 2]
            address = addr_lo & 0xFFFFFFFC
            address_type = addr_lo & 0x3
            data_start = idx + 3
        
        # Data
        data = b''
        if fmt & 0x2:  # Has data
            for dw in dws[data_start:]:
                data += dw.to_bytes(4, 'little')
        
        return TLPPacket(
            fmt=fmt,
            type=tlp_type,
            tc=tc,
            attr=attr,
            length=length,
            requester_id=requester_id,
            tag=tag,
            address=address,
            data=data,
            pasid_valid=pasid_valid,
            pasid_value=pasid_value,
            pasid_pmr=pasid_pmr,
            pasid_exe=pasid_exe,
            address_type=address_type
        )
    
    async def expect_read_request(self, timeout_ns=1000):
        """Wait for and return a memory read request."""
        packet = await cocotb.triggers.with_timeout(
            self.received.get(), 
            timeout_ns, 
            timeout_unit='ns'
        )
        assert packet.type == 0b00000, f"Expected MRd, got type {packet.type}"
        assert not (packet.fmt & 0b010), f"Expected no data, got fmt {packet.fmt}"
        return packet
    
    async def expect_write_request(self, timeout_ns=1000):
        """Wait for and return a memory write request."""
        packet = await cocotb.triggers.with_timeout(
            self.received.get(),
            timeout_ns,
            timeout_unit='ns'
        )
        assert packet.type == 0b00000, f"Expected MWr, got type {packet.type}"
        assert packet.fmt & 0b010, f"Expected data, got fmt {packet.fmt}"
        return packet
```

### Example Test Cases

```python
# verify/tb/test_dma.py

"""
cocotb tests for BSAExerciserDMACore
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer, ClockCycles

from bfm.wishbone import WishboneMaster
from bfm.tlp_stream import TLPSource, TLPSink


@cocotb.test()
async def test_dma_write_basic(dut):
    """Test basic DMA write (exerciser → host)."""
    
    # Start clock
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    # Reset
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    # Create BFMs
    tlp_sink = TLPSink(dut, prefix="tlp_tx_")
    tlp_source = TLPSource(dut, prefix="tlp_rx_")
    
    # Configure DMA
    dut.bus_addr.value = 0x0000_0001_0000_0000  # 64-bit address
    dut.local_offset.value = 0
    dut.length.value = 64  # 64 bytes
    dut.direction.value = 1  # Write (to host)
    dut.no_snoop.value = 0
    
    # Fill local memory with test pattern
    for i in range(8):
        dut.mem_rdata.value = 0xDEADBEEF_00000000 | i
        await RisingEdge(dut.clk)
    
    # Start DMA
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    
    # Expect memory write TLP
    packet = await tlp_sink.expect_write_request(timeout_ns=5000)
    
    # Verify TLP fields
    assert packet.address == 0x0000_0001_0000_0000, \
        f"Wrong address: {packet.address:#x}"
    assert packet.length == 16, \
        f"Wrong length: {packet.length} (expected 16 DW = 64 bytes)"
    assert (packet.attr & 0x1) == 0, \
        "No-snoop should be clear"
    
    dut._log.info(f"DMA write TLP: addr={packet.address:#x}, len={packet.length}")


@cocotb.test()
async def test_dma_with_pasid(dut):
    """Test DMA with PASID TLP prefix."""
    
    # Start clock
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    # Reset
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    # Create BFMs
    tlp_sink = TLPSink(dut, prefix="tlp_tx_")
    
    # Configure DMA with PASID
    dut.bus_addr.value = 0x0000_0001_0000_0000
    dut.local_offset.value = 0
    dut.length.value = 64
    dut.direction.value = 1
    dut.no_snoop.value = 0
    
    # Enable PASID
    dut.pasid_enable.value = 1
    dut.pasid_value.value = 0x00042  # PASID = 0x42
    dut.pasid_pmr.value = 1          # Privileged
    dut.pasid_exe.value = 0          # Data access
    
    # Start DMA
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    
    # Expect memory write TLP with PASID prefix
    packet = await tlp_sink.expect_write_request(timeout_ns=5000)
    
    # Verify PASID prefix
    assert packet.pasid_valid, "PASID prefix should be present"
    assert packet.pasid_value == 0x42, \
        f"Wrong PASID: {packet.pasid_value:#x}"
    assert packet.pasid_pmr == True, "PMR should be set"
    assert packet.pasid_exe == False, "Exe should be clear"
    
    dut._log.info(f"PASID TLP: pasid={packet.pasid_value:#x}, pmr={packet.pasid_pmr}")


@cocotb.test()
async def test_dma_read_completion(dut):
    """Test DMA read with completion handling."""
    
    # Start clock
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    # Reset
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    # Create BFMs
    tlp_sink = TLPSink(dut, prefix="tlp_tx_")
    tlp_source = TLPSource(dut, prefix="tlp_rx_")
    
    # Configure DMA read
    dut.bus_addr.value = 0x0000_0002_0000_0000
    dut.local_offset.value = 0x100
    dut.length.value = 32  # 32 bytes
    dut.direction.value = 0  # Read (from host)
    
    # Start DMA
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    
    # Expect read request
    req = await tlp_sink.expect_read_request(timeout_ns=5000)
    dut._log.info(f"Read request: addr={req.address:#x}, tag={req.tag}")
    
    # Send completion with test data
    test_data = bytes([i for i in range(32)])
    await tlp_source.send_completion(
        requester_id=req.requester_id,
        tag=req.tag,
        data=test_data
    )
    
    # Wait for DMA to complete
    for _ in range(100):
        await RisingEdge(dut.clk)
        if dut.done.value:
            break
    
    assert dut.done.value, "DMA should complete"
    assert not dut.error.value, "DMA should not error"


@cocotb.test()
async def test_no_snoop_attribute(dut):
    """Verify no-snoop attribute appears in TLP."""
    
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    tlp_sink = TLPSink(dut, prefix="tlp_tx_")
    
    # Configure with no-snoop
    dut.bus_addr.value = 0x1000_0000
    dut.length.value = 64
    dut.direction.value = 1
    dut.no_snoop.value = 1  # Enable no-snoop
    
    dut.start.value = 1
    await RisingEdge(dut.clk)
    dut.start.value = 0
    
    packet = await tlp_sink.expect_write_request()
    
    # Check NS bit in attributes (bit 0)
    assert packet.attr & 0x1, "No-snoop bit should be set"
    dut._log.info("No-snoop attribute verified")
```

### Test for ATS

```python
# verify/tb/test_ats.py

"""
cocotb tests for ATS functionality.
"""

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

from bfm.tlp_stream import TLPSource, TLPSink, TLPPacket


@cocotb.test()
async def test_ats_translation_request(dut):
    """Test that ATS generates translation request TLP."""
    
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    tlp_sink = TLPSink(dut, prefix="tlp_tx_")
    
    # Request translation
    dut.ats_req_valid.value = 1
    dut.ats_req_addr.value = 0x0000_0003_0000_0000  # VA to translate
    await RisingEdge(dut.clk)
    dut.ats_req_valid.value = 0
    
    # Expect translation request TLP
    packet = await tlp_sink.expect_read_request(timeout_ns=5000)
    
    # Verify AT field = 01 (Translation Request)
    assert packet.address_type == 0b01, \
        f"Expected AT=01, got {packet.address_type:02b}"
    assert packet.address == 0x0000_0003_0000_0000, \
        f"Wrong address in translation request"
    
    dut._log.info("ATS translation request verified")


@cocotb.test()
async def test_atc_hit(dut):
    """Test ATC lookup returns cached translation."""
    
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    # Pre-populate ATC entry via backdoor
    # (In real test, would receive translation completion first)
    # ATC entry: VA 0x3000_0000 → PA 0x8000_0000
    dut.atc_mem[0].value = 0x1  # Valid
    # ... populate entry fields ...
    
    # Lookup should hit
    dut.ats_lookup_req.value = 1
    dut.ats_lookup_addr.value = 0x0000_0003_0000_1234  # Same page
    await RisingEdge(dut.clk)
    
    # Wait for result
    for _ in range(10):
        await RisingEdge(dut.clk)
        if dut.ats_lookup_hit.value:
            break
    
    assert dut.ats_lookup_hit.value, "Should hit in ATC"
    # Verify translated address preserves page offset
    expected_pa = 0x0000_0008_0000_1234
    assert dut.ats_lookup_trans.value == expected_pa


@cocotb.test()
async def test_atc_invalidation(dut):
    """Test ATC invalidation clears entries."""
    
    clock = Clock(dut.clk, 8, units="ns")
    cocotb.start_soon(clock.start())
    
    dut.rst.value = 1
    await ClockCycles(dut.clk, 10)
    dut.rst.value = 0
    await ClockCycles(dut.clk, 10)
    
    # Pre-populate ATC
    dut.atc_mem[0].value = 0x1  # Valid entry
    
    # Send invalidation
    dut.inv_valid.value = 1
    dut.inv_global.value = 1  # Global invalidation
    await RisingEdge(dut.clk)
    dut.inv_valid.value = 0
    
    # Wait for ack
    while not dut.inv_ack.value:
        await RisingEdge(dut.clk)
    
    # Verify entry invalidated
    assert dut.atc_mem[0].value & 0x1 == 0, "Entry should be invalidated"
```

## Directory Structure

```
bsa_exerciser_spec_a7/
├── bsa_exerciser/
│   ├── __init__.py
│   ├── core.py              # Top-level Core (no primitives)
│   ├── regs.py              # RegsCore
│   ├── dma.py               # DMACore  
│   ├── ats.py               # ATSCore
│   └── wrappers.py          # Wrappers that connect to LitePCIe
│
├── verify/
│   ├── Makefile
│   ├── generate_verilog.py  # Migen → Verilog
│   │
│   ├── rtl/                 # Generated Verilog (git-ignored)
│   │   ├── bsa_exerciser_regs_core.v
│   │   ├── bsa_exerciser_dma_core.v
│   │   └── ...
│   │
│   ├── tb/
│   │   ├── bfm/
│   │   │   ├── __init__.py
│   │   │   ├── wishbone.py
│   │   │   └── tlp_stream.py
│   │   │
│   │   ├── test_regs.py
│   │   ├── test_dma.py
│   │   ├── test_ats.py
│   │   └── test_integration.py
│   │
│   └── waves/               # Waveform dumps
│
└── spec_a7_bsa_exerciser.py # Build script (uses wrappers)
```

## Key Principles

1. **Core modules have NO vendor primitives**
   - Pure Migen/LiteX logic only
   - BRAMs are OK (they're portable)
   - NO: MMCM, BUFG, IDELAYE, GTP/GTX

2. **Core modules accept SIGNALS, not MODULE INSTANCES**
   - `def __init__(self, data_width=64)` ✅
   - `def __init__(self, pcie_endpoint)` ❌ (in Core)

3. **Wrappers handle the messy integration**
   - Connect Core to actual LitePCIe
   - Handle clock domain crossings
   - Instantiate vendor primitives

4. **Verilog generation is explicit**
   - Run `generate_verilog.py` before simulation
   - Generated RTL is checked in CI, not Git

5. **cocotb BFMs match interface exactly**
   - Signal names must match Migen output
   - Use Record layouts where possible
