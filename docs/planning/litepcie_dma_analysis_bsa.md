# LitePCIe DMA Analysis for BSA Exerciser

## Executive Summary

**Recommendation:** Implement a new, simple BSA DMA module rather than adapting the existing LitePCIe DMA. The architectural mismatch is fundamental - LitePCIe DMA is optimized for streaming, while BSA needs simple register-triggered transactions.

The good news: LitePCIe already contains simpler patterns (MSI-X, DMA Status) that are excellent templates for a BSA DMA engine.

---

## Part 1: Existing LitePCIe DMA Architecture

### 1.1 Design Philosophy

The LitePCIe DMA is designed for **high-throughput streaming** applications:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    LitePCIe DMA Architecture                             │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Host Memory                          FPGA                              │
│  ───────────                          ────                              │
│                                                                         │
│  ┌──────────────┐                    ┌────────────────────────────┐    │
│  │  Descriptor  │ ──── CSR Write ──► │    Scatter-Gather Table    │    │
│  │    Table     │                    │  (FIFO of descriptors)     │    │
│  │ addr,len,ctl │                    └─────────────┬──────────────┘    │
│  └──────────────┘                                  │                    │
│                                                    ▼                    │
│                                       ┌────────────────────────────┐    │
│  ┌──────────────┐                    │   Descriptor Splitter      │    │
│  │   DMA Data   │                    │   (max_payload/request)    │    │
│  │   Buffers    │ ◄─── PCIe TLP ──── └─────────────┬──────────────┘    │
│  │              │                                  │                    │
│  └──────────────┘                                  ▼                    │
│        ▲                              ┌────────────────────────────┐    │
│        │                              │      DMA Reader/Writer     │    │
│        │                              │  ┌─────────────────────┐   │    │
│        │                              │  │     Data FIFO       │   │    │
│        │                              │  └──────────┬──────────┘   │    │
│        │                              │             │              │    │
│        └───────────────────────────── │  ┌──────────▼──────────┐   │    │
│             PCIe TLP (completions)    │  │   Stream Interface  │   │    │
│                                       │  │   (sink/source)     │   │    │
│                                       │  └──────────┬──────────┘   │    │
│                                       └─────────────┼──────────────┘    │
│                                                     │                   │
│                                                     ▼                   │
│                                       ┌────────────────────────────┐    │
│                                       │     User Logic             │    │
│                                       │  (SDR, Video, etc.)        │    │
│                                       └────────────────────────────┘    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 1.2 Key Components

#### Scatter-Gather Table (`LitePCIeDMAScatterGather`)
**File:** `litepcie/frontend/dma.py` lines 2815-2955

```python
class LitePCIeDMAScatterGather(LiteXModule):
    """
    Software programmable table storing a list of DMA descriptors.
    
    Modes:
    - Prog mode: Host fills table via CSR writes
    - Loop mode: Descriptors automatically refilled after execution
    """
    
    # CSRs for table programming
    self.value = CSRStorage(64)     # Address + length + control
    self.we = CSRStorage(32)         # Write enable + address MSB
    self.loop_prog_n = CSRStorage()  # Mode selection
    self.loop_status = CSRStatus()   # Current index/count
```

The table is implemented as a **SyncFIFO** - descriptors are queued and consumed sequentially.

#### Descriptor Splitter (`LitePCIeDMADescriptorSplitter`)
**File:** `litepcie/frontend/dma.py` lines 2960-3036

Splits large descriptors (up to 16MB) into PCIe-sized chunks:
- Writes: Limited to `max_payload_size` (typically 128-512 bytes)
- Reads: Limited to `max_request_size` (typically 128-512 bytes)

```python
# Example: 4KB transfer with 256-byte max_payload
# Original: addr=0x1000, len=4096
# Split into: (0x1000, 256), (0x1100, 256), ..., (0x1F00, 256)
```

#### DMA Reader (`LitePCIeDMAReader`)
**File:** `litepcie/frontend/dma.py` lines 3040-3182

Generates memory read TLPs, routes completion data to stream source:

```python
# Flow:
# 1. Get descriptor from table
# 2. Split into max_request_size chunks
# 3. Issue Memory Read TLPs
# 4. Collect completions into Data FIFO
# 5. Output to stream source

# Key signals:
self.source = stream.Endpoint(dma_layout(data_width))  # Data output
self.irq = Signal()  # Interrupt on descriptor completion
```

#### DMA Writer (`LitePCIeDMAWriter`)
**File:** `litepcie/frontend/dma.py` lines 3186-3310

Receives stream data, generates memory write TLPs:

```python
# Flow:
# 1. Get descriptor from table
# 2. Split into max_payload_size chunks
# 3. Collect data from stream sink into FIFO
# 4. Issue Memory Write TLPs when enough data available
# 5. IRQ on completion

# Key signals:
self.sink = stream.Endpoint(dma_layout(data_width))  # Data input
self.irq = Signal()  # Interrupt on descriptor completion
```

### 1.3 Control Flow

```
Host Software                              FPGA
─────────────                              ────

1. Allocate DMA buffers
   (physically contiguous)

2. Write descriptors to table:
   for each buffer:
       write(value, addr | len << 32)  ─────►  Table FIFO enqueue
       write(we, addr_msb)

3. Set loop mode:
   write(loop_prog_n, 1)               ─────►  Enable auto-refill

4. Enable DMA:
   write(enable, 1)                    ─────►  FSM starts

5. Wait for completion:                ◄─────  IRQ or poll loop_status

6. Process data / check status
```

---

## Part 2: BSA Exerciser Requirements

### 2.1 BSA DMA Model

The BSA exerciser needs a fundamentally different model:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    BSA Exerciser DMA Model                               │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Host Memory                          FPGA                              │
│  ───────────                          ────                              │
│                                                                         │
│  ┌──────────────┐                    ┌────────────────────────────┐    │
│  │   Test       │                    │     MMIO Registers         │    │
│  │   Buffer     │                    │  ┌─────────────────────┐   │    │
│  │              │                    │  │ DMA_ADDRESS  [63:0] │   │    │
│  └──────────────┘                    │  │ DMA_LENGTH   [23:0] │   │    │
│        ▲                             │  │ DMA_CONTROL  [31:0] │◄──┼─ CSR Write
│        │                             │  │   - trigger  [0]    │   │    │
│        │                             │  │   - direction[4]    │   │    │
│        │                             │  │   - no_snoop [5]    │   │    │
│        │                             │  │   - pasid_en [6]    │   │    │
│        │                             │  │   - ...             │   │    │
│        │                             │  │ PASID_VALUE  [19:0] │   │    │
│        │                             │  │ DMA_STATUS   [31:0] │───┼─► CSR Read
│        │                             │  └─────────────────────┘   │    │
│        │                             └─────────────┬──────────────┘    │
│        │                                           │ trigger           │
│        │                                           ▼                   │
│        │                             ┌────────────────────────────┐    │
│        │                             │    Simple DMA Engine       │    │
│        │                             │                            │    │
│        └──── Single PCIe TLP ─────── │  FSM: IDLE → EXECUTE →     │    │
│                (per trigger)         │       WAIT_CMP → DONE      │    │
│                                      │                            │    │
│                                      │  - Generate single TLP     │    │
│                                      │  - Set attributes from reg │    │
│                                      │  - Add PASID prefix if en  │    │
│                                      │  - Wait for completion     │    │
│                                      │  - Report status           │    │
│                                      └────────────────────────────┘    │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 BSA Register Map (from ARM spec)

```
Offset  Name             Description
──────  ────             ───────────
0x00    DMA_CONTROL      Control and trigger
          [3:0]  trigger        Write 0x1 to start DMA
          [4]    direction      0=read from host, 1=write to host
          [5]    no_snoop       No-Snoop attribute
          [6]    pasid_en       Enable PASID prefix
          [7]    privileged     PMR bit (with PASID)
          [8]    instruction    Execute bit (with PASID)
          [9]    use_atc        Use ATC for translation
          [10]   addr_type      0=untranslated, 1=translated
          
0x08    DMA_ADDRESS      64-bit target address in host memory
0x10    DMA_LENGTH       Transfer length in bytes
0x18    DMA_STATUS       Completion status, error flags
0x20    PASID_VALUE      20-bit PASID value
0x28    MSI_CONTROL      MSI trigger register
0x30    TRACE_CONTROL    Transaction trace control
...
```

### 2.3 Key Differences

| Aspect | LitePCIe DMA | BSA Exerciser |
|--------|--------------|---------------|
| **Trigger** | Enable bit + descriptor table | Single register write |
| **Transfer count** | Multiple (scatter-gather) | Single per trigger |
| **Data handling** | Stream to/from user logic | Discard (read) or pattern (write) |
| **Attributes** | Hardcoded to 0 | Per-transaction control |
| **PASID** | Not supported | Required for SMMU testing |
| **AT field** | Always 0 (untranslated) | Configurable for ATS testing |
| **Completion** | Via IRQ and loop_status | Via status register and IRQ |
| **Complexity** | High (streaming, buffering) | Low (simple FSM) |

---

## Part 3: Why Existing DMA Won't Work

### 3.1 Architectural Mismatches

1. **No Attribute Control**
   - LitePCIe hardcodes `tlp_req.attr.eq(0)` in packetizer
   - BSA needs per-transaction NS, RO, IDO control

2. **No PASID Support**
   - LitePCIe has no concept of TLP prefixes
   - BSA requires PASID for SMMU/SVA testing

3. **Streaming Data Model**
   - LitePCIe DMA connects to user logic via stream interface
   - BSA doesn't need actual data - reads are discarded, writes use patterns
   
4. **Table-Based Descriptors**
   - LitePCIe requires preprogramming a descriptor table
   - BSA wants direct register-to-TLP mapping

5. **Complex State Machine**
   - LitePCIe handles pending requests, FIFOs, converter, splitter
   - BSA just needs: idle → issue TLP → wait completion → done

### 3.2 The `with_table=False` Mode

LitePCIe DMA has a `with_table=False` option that exposes a `desc_sink` for direct descriptor injection:

```python
# From LitePCIeDMAReader.__init__
if with_table:
    self.table = LitePCIeDMAScatterGather(...)
else:
    self.desc_sink = stream.Endpoint(descriptor_layout())  # Direct injection
```

**However**, this still doesn't solve the fundamental issues:
- Still requires streaming data interface
- Still no attribute control
- Still goes through splitter (unnecessary for BSA)
- Would need significant modification anyway

---

## Part 4: Better Templates in LitePCIe

### 4.1 MSI-X as a Pattern

**File:** `litepcie/core/msi.py` (LitePCIeMSIX class)

MSI-X shows the exact pattern we need for BSA:

```python
class LitePCIeMSIX(LiteXModule):
    def __init__(self, endpoint, width=32):
        # Get a master port for TLP generation
        self.port = port = endpoint.crossbar.get_master_port()
        
        # Simple FSM
        self.fsm = fsm = FSM(reset_state="IDLE")
        
        fsm.act("IDLE",
            If(msix_valid,
                NextState("ISSUE-WRITE")
            )
        )
        
        # Set TLP parameters directly
        self.comb += [
            port.source.channel.eq(port.channel),
            port.source.first.eq(1),
            port.source.last.eq(1),
            port.source.adr.eq(msix_adr),      # From table lookup
            port.source.req_id.eq(endpoint.phy.id),
            port.source.tag.eq(0),
            port.source.len.eq(1),
            port.source.dat.eq(msix_dat),
        ]
        
        fsm.act("ISSUE-WRITE",
            port.source.valid.eq(1),
            port.source.we.eq(1),
            If(port.source.ready,
                NextState("IDLE")
            )
        )
```

**Key insight:** This is a ~50 line module that issues memory write TLPs from register values. BSA DMA is essentially the same pattern with:
- Read support (we=0, wait for completion)
- Attribute fields
- PASID prefix option

### 4.2 DMA Status as a Pattern

**File:** `litepcie/frontend/dma.py` (LitePCIeDMAStatus class)

Shows register-triggered multi-DWORD writes:

```python
class LitePCIeDMAStatus(LiteXModule):
    def __init__(self, endpoint, ...):
        self.control = CSRStorage(...)
        self.address_lsb = CSRStorage(32)
        self.address_msb = CSRStorage(32)
        
        port = endpoint.crossbar.get_master_port(write_only=True)
        
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(self.control.fields.enable & update,
                NextState("WORDS-WRITE")
            )
        )
        
        # Configure port from CSR values
        self.sync += [
            port.source.adr.eq(self.address_lsb.storage + ...),
            port.source.len.eq(dwords),
            port.source.dat.eq(status[offset]),
        ]
        
        fsm.act("WORDS-WRITE",
            port.source.valid.eq(1),
            port.source.we.eq(1),
            If(port.source.ready,
                NextState("WORDS-UPDATE")
            )
        )
```

---

## Part 5: Recommended BSA DMA Implementation

### 5.1 Architecture Overview

```python
class LitePCIeBSADMA(LiteXModule):
    """
    Simple MMIO-triggered DMA for BSA/SBSA PCIe Exerciser.
    
    Differences from LitePCIeDMA:
    - Direct register control (no descriptor table)
    - Per-transaction attribute control (NS, RO)
    - PASID prefix support
    - AT field support for ATS testing
    - No streaming interface (data discarded/pattern generated)
    """
```

### 5.2 Proposed Implementation

```python
from migen import *
from litex.gen import *
from litex.soc.interconnect.csr import *
from litepcie.common import *
from litepcie.tlp.common import max_request_size, max_payload_size

class LitePCIeBSADMA(LiteXModule):
    def __init__(self, endpoint, address_width=64):
        # =========================================================================
        # CSR Interface (BSA Register Map)
        # =========================================================================
        
        self.control = CSRStorage(32, fields=[
            CSRField("trigger",     size=4,  offset=0,  description="Write 0x1 to trigger DMA"),
            CSRField("direction",   size=1,  offset=4,  description="0=read, 1=write"),
            CSRField("no_snoop",    size=1,  offset=5,  description="No-Snoop attribute"),
            CSRField("relaxed_ord", size=1,  offset=6,  description="Relaxed Ordering attribute"),
            CSRField("pasid_en",    size=1,  offset=7,  description="Enable PASID TLP prefix"),
            CSRField("privileged",  size=1,  offset=8,  description="PASID Privileged Mode"),
            CSRField("instruction", size=1,  offset=9,  description="PASID Execute/Instruction"),
            CSRField("addr_type",   size=2,  offset=10, description="Address Type (AT) field"),
        ])
        
        self.address_lo = CSRStorage(32, description="DMA target address [31:0]")
        self.address_hi = CSRStorage(32, description="DMA target address [63:32]")
        self.length     = CSRStorage(24, description="Transfer length in bytes")
        self.pasid      = CSRStorage(20, description="PASID value")
        
        self.status = CSRStatus(32, fields=[
            CSRField("busy",     size=1,  offset=0,  description="DMA in progress"),
            CSRField("done",     size=1,  offset=1,  description="DMA completed"),
            CSRField("error",    size=1,  offset=2,  description="Completion error"),
            CSRField("cpl_status", size=3, offset=4, description="Completion status"),
        ])
        
        # IRQ output
        self.irq = Signal()
        
        # =========================================================================
        # Internal Signals
        # =========================================================================
        
        # Get master port from endpoint
        # Note: NOT write_only because we need completions for reads
        self.port = port = endpoint.crossbar.get_master_port()
        
        # Transfer tracking
        address       = Signal(64)
        length        = Signal(24)
        remaining     = Signal(24)
        chunk_len     = Signal(10)  # Current chunk length in DWORDs
        is_write      = Signal()
        tag_counter   = Signal(8)
        pending_reads = Signal(8)
        
        # Attributes
        attr_ns = Signal()
        attr_ro = Signal()
        attr_at = Signal(2)
        
        # Status
        busy = Signal()
        done = Signal()
        error = Signal()
        cpl_status = Signal(3)
        
        self.comb += [
            self.status.fields.busy.eq(busy),
            self.status.fields.done.eq(done),
            self.status.fields.error.eq(error),
            self.status.fields.cpl_status.eq(cpl_status),
        ]
        
        # =========================================================================
        # Trigger Detection
        # =========================================================================
        
        trigger = Signal()
        trigger_prev = Signal()
        self.sync += trigger_prev.eq(self.control.fields.trigger)
        self.comb += trigger.eq(
            (self.control.fields.trigger == 0x1) & 
            (trigger_prev != 0x1)
        )
        
        # =========================================================================
        # Chunk Size Calculation
        # =========================================================================
        
        max_read_dwords  = max_request_size // 4
        max_write_dwords = max_payload_size // 4
        
        # Calculate chunk size (in DWORDs)
        remaining_dwords = Signal(22)
        self.comb += remaining_dwords.eq(remaining[2:])  # Bytes to DWORDs
        
        self.comb += [
            If(is_write,
                If(remaining_dwords > max_write_dwords,
                    chunk_len.eq(max_write_dwords)
                ).Else(
                    chunk_len.eq(remaining_dwords)
                )
            ).Else(
                If(remaining_dwords > max_read_dwords,
                    chunk_len.eq(max_read_dwords)
                ).Else(
                    chunk_len.eq(remaining_dwords)
                )
            )
        ]
        
        # =========================================================================
        # Port Signals
        # =========================================================================
        
        self.comb += [
            port.source.channel.eq(port.channel),
            port.source.first.eq(1),
            port.source.last.eq(1),
            port.source.req_id.eq(endpoint.phy.id),
            port.source.adr.eq(address),
            port.source.len.eq(chunk_len),
            port.source.tag.eq(tag_counter),
            port.source.we.eq(is_write),
            
            # NEW: Attribute fields (requires request_layout modification)
            # port.source.attr.eq(Cat(attr_ns, attr_ro)),
            # port.source.at.eq(attr_at),
            
            # For writes: generate pattern data (e.g., address-based)
            port.source.dat.eq(address[:endpoint.phy.data_width]),
        ]
        
        # =========================================================================
        # FSM
        # =========================================================================
        
        self.fsm = fsm = FSM(reset_state="IDLE")
        
        fsm.act("IDLE",
            done.eq(0),
            If(trigger,
                # Latch parameters
                NextValue(address, Cat(self.address_lo.storage, self.address_hi.storage)),
                NextValue(length, self.length.storage),
                NextValue(remaining, self.length.storage),
                NextValue(is_write, self.control.fields.direction),
                NextValue(attr_ns, self.control.fields.no_snoop),
                NextValue(attr_ro, self.control.fields.relaxed_ord),
                NextValue(attr_at, self.control.fields.addr_type),
                NextValue(pending_reads, 0),
                NextValue(error, 0),
                NextValue(busy, 1),
                NextState("ISSUE-TLP")
            )
        )
        
        fsm.act("ISSUE-TLP",
            port.source.valid.eq(1),
            If(port.source.ready,
                # Update address and remaining
                NextValue(address, address + (chunk_len << 2)),
                NextValue(remaining, remaining - (chunk_len << 2)),
                
                If(~is_write,
                    # Track pending read completions
                    NextValue(pending_reads, pending_reads + 1),
                    NextValue(tag_counter, tag_counter + 1),
                ),
                
                If(remaining <= (chunk_len << 2),
                    # Last chunk
                    If(is_write,
                        # Writes: done immediately (posted)
                        NextState("COMPLETE")
                    ).Else(
                        # Reads: wait for completions
                        NextState("WAIT-COMPLETIONS")
                    )
                ).Else(
                    # More chunks to issue
                    NextState("ISSUE-TLP")
                )
            )
        )
        
        fsm.act("WAIT-COMPLETIONS",
            # Accept completion data
            port.sink.ready.eq(1),
            
            If(port.sink.valid,
                # Check for errors
                If(port.sink.err,
                    NextValue(error, 1),
                ),
                If(port.sink.end,
                    # This completion packet finished a request
                    NextValue(pending_reads, pending_reads - 1),
                )
            ),
            
            If(pending_reads == 0,
                NextState("COMPLETE")
            )
        )
        
        fsm.act("COMPLETE",
            NextValue(busy, 0),
            NextValue(done, 1),
            self.irq.eq(1),  # Pulse IRQ
            NextState("IDLE")
        )
```

### 5.3 Integration Points

To fully implement this, we also need:

1. **Extend `request_layout`** in `litepcie/common.py`:
```python
def request_layout(data_width, address_width=32):
    layout = [
        # ... existing fields ...
        ("attr", 3),  # NS, RO, IDO
        ("at",   2),  # Address Type
    ]
```

2. **Modify packetizer** in `litepcie/tlp/packetizer.py`:
```python
# Change from:
tlp_req.attr.eq(0),

# To:
tlp_req.attr.eq(req_sink.attr),
```

3. **Add PASID prefix support** (more complex, Phase 2)

---

## Part 6: Implementation Phases

### Phase 1: Basic BSA DMA (Low Risk)
- New `LitePCIeBSADMA` module
- Extend port interface with `attr` field
- Wire through packetizer
- Test on x86 with standard memory read/writes
- Verify NS bit via TLP analyzer or driver tracing

**Estimated effort:** 2-3 days

### Phase 2: PASID Support (Medium Risk)
- Add PASID prefix insertion in packetizer
- Add `pasid_valid`, `pasid`, `pasid_pmr`, `pasid_exe` to port
- Test with SMMU in passthrough mode first
- Then test with actual PASID validation

**Estimated effort:** 1 week

### Phase 3: ATS Support (Higher Risk)
- AT field handling in packetizer
- ATS completion parsing in depacketizer
- Simple ATC implementation
- Invalidation handling

**Estimated effort:** 2 weeks

### Phase 4: Transaction Monitoring
- Snoop incoming TLPs in depacketizer
- FIFO for trace entries
- CSR interface for trace readout

**Estimated effort:** 1 week

---

## Appendix: Code Comparison

### LitePCIe DMA Writer (Streaming)
```python
# ~150 lines
# - Scatter-gather table management
# - Descriptor splitting
# - Data FIFO buffering  
# - Stream converter
# - Flow control for backpressure
# - Multiple pending requests
```

### Proposed BSA DMA
```python
# ~100 lines
# - Direct register control
# - Simple single-TLP-at-a-time
# - No data buffering needed
# - Attribute passthrough
# - Clear completion tracking
```

The BSA DMA is actually **simpler** than the existing LitePCIe DMA despite having more TLP-level features, because it doesn't need the streaming infrastructure.

---

## Conclusion

**Recommendation: Create a new, purpose-built BSA DMA module.**

Rationale:
1. Cleaner architecture - no adaptation of mismatched abstractions
2. Simpler implementation - follows MSI-X pattern
3. Easier testing - direct register-to-TLP mapping
4. Better maintainability - clearly separate from streaming DMA
5. Potentially upstreamable - adds BSA capability without breaking existing users

The existing LitePCIe DMA should remain unchanged for its intended streaming use cases. The BSA DMA would be a parallel frontend option.
