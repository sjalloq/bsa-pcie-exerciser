# LitePCIe BSA Exerciser Implementation Analysis

## Executive Summary

This document provides a detailed technical analysis of the LitePCIe codebase to support implementing an ARM BSA/SBSA PCIe Exerciser. All investigation questions from the original guide have been answered based on actual source code review.

**Key Finding:** The architecture is well-structured for extension. The primary modification point for BSA TLP attribute control is a single hardcoded line in the packetizer.

---

## Part 1: Verified Interface Definitions

### 1.1 Request Layout (port.source for master ports)

**File:** `litepcie/common.py` (lines 1530-1546)

```python
def request_layout(data_width, address_width=32):
    layout = [
        # Request Parameters
        ("req_id",          16),  # Requester ID (assigned by endpoint)
        ("we",               1),  # Write Enable: 0=Read, 1=Write
        ("adr",  address_width),  # Address in BYTES (not DW-aligned at port level!)
        ("len",             10),  # Length in DWORDS
        ("tag",              8),  # Tag for matching completions

        # Data Stream
        ("dat",     data_width),  # Data payload (for writes)

        # Internal Routing
        ("channel",          8),  # Crossbar channel (auto-assigned)
        ("user_id",          8),  # Packet ID for multi-beat tracking
    ]
    return EndpointDescription(layout)
```

**Critical Observation:** No `attr` (attributes) field exists at the port level. The attributes (NS, RO, IDO) are hardcoded in the packetizer.

### 1.2 Completion Layout (port.sink for master ports)

**File:** `litepcie/common.py` (lines 1548-1566)

```python
def completion_layout(data_width, address_width=32):
    layout = [
        # Completion Parameters
        ("req_id",          16),  # Original requester ID
        ("cmp_id",          16),  # Completer ID
        ("adr",  address_width),  # Lower address
        ("len",             10),  # Remaining length in DWORDS
        ("end",              1),  # Last completion for this request
        ("err",              1),  # Completion error (UR, CA, etc.)
        ("tag",              8),  # Tag to match with request

        # Data Stream
        ("dat",     data_width),  # Completion data

        # Internal Routing
        ("channel",          8),  # Crossbar channel
        ("user_id",          8),  # Packet ID
    ]
    return EndpointDescription(layout)
```

### 1.3 Port Allocation via Crossbar

**File:** `litepcie/core/crossbar.py` (lines 2245-2255)

```python
def get_master_port(self, write_only=False, read_only=False):
    m = LitePCIeMasterInternalPort(
        data_width    = self.data_width,
        address_width = self.address_width,
        channel       = self.user_masters_channel,  # Auto-incremented
        write_only    = write_only,
        read_only     = read_only
    )
    self.user_masters_channel += 1
    self.user_masters.append(m)
    return LitePCIeMasterPort(m)
```

**Key Insight:** Each port gets a unique channel number for completion routing. Write-only ports bypass the pending request controller (important for BSA - we may want RW ports for most operations).

---

## Part 2: TLP Generation Analysis

### 2.1 TLP Request Header Fields

**File:** `litepcie/tlp/common.py` (lines 15771-15792)

```python
tlp_request_header_fields = {
    # DW0 (Byte 0-3)
    "fmt"          : HeaderField(byte=0*4, offset=29, width= 2),  # Format (3DW/4DW, data/no-data)
    "type"         : HeaderField(byte=0*4, offset=24, width= 5),  # Type (Memory, Config, etc.)
    "tc"           : HeaderField(byte=0*4, offset=20, width= 3),  # Traffic Class
    "td"           : HeaderField(byte=0*4, offset=15, width= 1),  # TLP Digest present
    "ep"           : HeaderField(byte=0*4, offset=14, width= 1),  # Poisoned
    "attr"         : HeaderField(byte=0*4, offset=12, width= 2),  # ATTRIBUTES (NS, RO)
    "length"       : HeaderField(byte=0*4, offset= 0, width=10),  # Length in DW
    
    # DW1 (Byte 4-7)
    "requester_id" : HeaderField(byte=1*4, offset=16, width=16),  # Requester ID
    "tag"          : HeaderField(byte=1*4, offset= 8, width= 8),  # Tag
    "last_be"      : HeaderField(byte=1*4, offset= 4, width= 4),  # Last DW Byte Enable
    "first_be"     : HeaderField(byte=1*4, offset= 0, width= 4),  # First DW Byte Enable
    
    # DW2-3 (Byte 8-15) - Address
    "address"      : HeaderField(byte=2*4, offset= 0, width=64),  # 32 or 64-bit address
}
```

**Note on attr field:** Per PCIe spec:
- `attr[0]` = No Snoop (NS)
- `attr[1]` = Relaxed Ordering (RO)
- (attr[2] = IDO is in a different location for some TLP types)

### 2.2 Where Attributes Are Hardcoded

**File:** `litepcie/tlp/packetizer.py` (lines 17517-17521)

```python
self.comb += [
    tlp_req.tc.eq(0),       # Traffic Class = 0
    tlp_req.td.eq(0),       # No TLP Digest
    tlp_req.ep.eq(0),       # Not poisoned
    tlp_req.attr.eq(0),     # <-- ATTRIBUTES HARDCODED TO 0 (no NS, no RO)
    tlp_req.length.eq(req_sink.len),
    # ...
]
```

**THIS IS THE PRIMARY MODIFICATION POINT** for BSA attribute support.

### 2.3 Address Handling and AT Field

**File:** `litepcie/tlp/packetizer.py` (lines 17481, 17496-17497)

For 32-bit addressing:
```python
tlp_req.address.eq(req_sink.adr),
```

For 64-bit addressing:
```python
# Address bytes are swapped for 64-bit format
tlp_req.address[:32].eq(req_sink.adr[32:]),  # Upper 32 bits -> DW2
tlp_req.address[32:].eq(req_sink.adr[:32]),  # Lower 32 bits -> DW3
```

**AT Field Analysis:**
The PCIe spec places AT (Address Type) in bits [1:0] of the address. Currently:
- The address from the port is passed directly
- No explicit AT field handling exists
- Default behavior is AT=00 (Untranslated)

To support AT, we need to either:
1. Add an explicit `at` field to the port interface, OR
2. Allow the lower 2 bits of address to carry AT (requires careful handling)

### 2.4 PASID Prefix Insertion Point

**Current TLP assembly flow:**

```
req_sink -> tlp_req (format/encode) -> tlp_raw_req -> header_inserter -> phy.sink
```

**File:** `litepcie/tlp/packetizer.py` (lines 17692-17710)

```python
# Insert header
header_inserter_cls = {
    64  : LitePCIeTLPHeaderInserter64b,
    128 : LitePCIeTLPHeaderInserter128b,
    256 : LitePCIeTLPHeaderInserter256b,
    512 : LitePCIeTLPHeaderInserter512b,
}
header_inserter = header_inserter_cls[data_width](fmt=tlp_raw_d.fmt)
self.submodules += header_inserter
self.comb += tlp_raw_d.connect(header_inserter.sink)
self.comb += header_inserter.source.connect(self.source, omit={"data", "be"})
```

**PASID Implementation Strategy:**
PASID prefix would need to be inserted BEFORE the header inserter. Options:
1. Create a `LitePCIePASIDPrefixInserter` that sits between tlp_raw_d and header_inserter
2. Modify the header inserter classes to conditionally prepend PASID prefix
3. Handle PASID at the PHY level (if PHY supports it natively - Xilinx USP does)

---

## Part 3: TLP Reception Analysis

### 3.1 Depacketizer Architecture

**File:** `litepcie/tlp/depacketizer.py`

The depacketizer extracts TLP headers and dispatches to different sinks based on type:

```python
# Dispatcher routes TLPs based on fmt_type
dispatch_sources = {"DISCARD": ..., "REQUEST": ..., "COMPLETION": ..., ...}

# Routing logic
self.comb += [
    If(fmt_type == fmt_type_dict["mem_rd32"], dispatcher.sel.eq(REQUEST)),
    If(fmt_type == fmt_type_dict["cpld"],     dispatcher.sel.eq(COMPLETION)),
    # ...
]
```

### 3.2 Completion Routing

Completions are routed back to the originating port via the crossbar's channel field:

**File:** `litepcie/core/crossbar.py` (lines 2286-2290)

```python
m_dispatcher = Dispatcher(master.source, m_sources, one_hot=True)
for i, m in enumerate(masters):
    if m.channel is not None:
        self.comb += m_dispatcher.sel[i].eq(master.source.channel == m.channel)
```

**ATS Completion Interception:**
To intercept ATS completions, we would need to:
1. Add "ATS" to depacketizer capabilities
2. Create dispatch logic to identify ATS completion format
3. Route to a dedicated ATS handler module

---

## Part 4: MSI/MSI-X Analysis

### 4.1 MSI-X Implementation

**File:** `litepcie/core/msi.py` (LitePCIeMSIX class, around line 2520)

```python
class LitePCIeMSIX(LiteXModule):
    def __init__(self, endpoint, width=32, default_enable=True):
        self.irqs = Signal(width)
        
        # Get a master port for sending MSI-X writes
        self.port = port = endpoint.crossbar.get_master_port()
        
        # MSI-X table in memory
        self.specials.table = Memory(4*32, width, init=[...])
        
        # FSM sends memory write TLPs to trigger MSI-X
        fsm.act("ISSUE-WRITE",
            port.source.valid.eq(~msix_mask),
            port.source.we.eq(1),
            port.source.adr.eq(msix_adr),   # From table entry
            port.source.dat.eq(msix_dat),   # From table entry
            # ...
        )
```

**Key Pattern for BSA:** MSI-X already shows how to:
1. Get a master port
2. Programmatically generate memory write TLPs
3. Use CSRs to control behavior

### 4.2 Triggering Arbitrary MSI Vectors

The MSI modules use an `irqs` signal where each bit represents a pending interrupt:

```python
self.irqs = Signal(width)  # Set bit N to trigger MSI vector N
```

For BSA, we can:
1. Create a CSR that writes to specific bits of the irqs signal
2. Or directly generate MSI TLPs through a dedicated master port

---

## Part 5: Implementation Plan

### Phase 1: Add Attribute Support to Port Interface

**Modification 1:** `litepcie/common.py`

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
        # NEW FIELDS FOR BSA
        ("attr",             3),  # NS[0], RO[1], IDO[2]
        ("at",               2),  # Address Type for ATS
    ]
    return EndpointDescription(layout)
```

**Modification 2:** `litepcie/tlp/packetizer.py` (line 17521)

```python
# BEFORE:
tlp_req.attr.eq(0),

# AFTER:
tlp_req.attr.eq(req_sink.attr[:2]),  # Pass through NS and RO
```

**Impact Assessment:** 
- Low risk - existing code doesn't set these fields, so defaults to 0
- Backward compatible - existing frontends continue to work

### Phase 2: Create BSA Exerciser Frontend

Create new file: `litepcie/frontend/bsa_exerciser.py`

```python
class LitePCIeBSAExerciser(LiteXModule):
    """BSA/SBSA PCIe Exerciser for ARM compliance testing."""
    
    def __init__(self, endpoint, address_width=64):
        # Get master port for DMA operations
        self.port = port = endpoint.crossbar.get_master_port()
        
        # CSRs per BSA spec
        self.dma_control = CSRStorage(32, fields=[
            CSRField("trigger",     size=4),   # Write 0x1 to trigger
            CSRField("direction",   size=1),   # 0=read, 1=write
            CSRField("no_snoop",    size=1),   # No-Snoop attribute
            CSRField("pasid_en",    size=1),   # Enable PASID
            CSRField("privileged",  size=1),   # Privileged mode
            CSRField("instruction", size=1),   # Instruction fetch
            CSRField("use_atc",     size=1),   # Use ATC
            CSRField("addr_type",   size=1),   # 0=untranslated, 1=translated
        ])
        self.dma_address = CSRStorage(64)
        self.dma_length  = CSRStorage(24)
        self.pasid_value = CSRStorage(20)
        
        # FSM to execute DMA
        self.fsm = fsm = FSM(reset_state="IDLE")
        # ... implementation
```

### Phase 3: Add PASID Prefix Support (Complex)

This requires deeper modifications to the packetizer's header insertion logic.

**Option A: PHY-Level PASID (Preferred for Xilinx USP)**
If using Xilinx UltraScale+ with the native PCIe IP, PASID can be handled at the PHY interface level. Check the `litepcie/phy/usppciephy.py` for AXI-Stream TLP format which may support PASID directly.

**Option B: Soft PASID Prefix Insertion**
Create a new module that prepends PASID prefix bytes to the TLP stream before the header inserter.

### Phase 4: ATS Support (Most Complex)

Requires:
1. New capability in depacketizer for ATS completions
2. ATC (Address Translation Cache) implementation
3. Invalidation request handling
4. Integration with config space for ATS capability advertisement

---

## Part 6: Modification Points Summary

| File | Line(s) | Change Required | Complexity |
|------|---------|-----------------|------------|
| `litepcie/common.py` | 1530-1546 | Add `attr`, `at` fields to request_layout | Low |
| `litepcie/tlp/packetizer.py` | 17521 | Use `req_sink.attr` instead of hardcoded 0 | Low |
| `litepcie/tlp/packetizer.py` | 17481 | Handle AT bits in address | Medium |
| `litepcie/tlp/packetizer.py` | 17692-17710 | Add PASID prefix insertion | High |
| `litepcie/tlp/depacketizer.py` | various | Add ATS completion parsing | High |
| `litepcie/core/msi.py` | N/A | Add BSA MSI trigger CSR | Low |
| NEW: `litepcie/frontend/bsa_exerciser.py` | N/A | Complete BSA exerciser module | Medium |

---

## Part 7: Test Strategy

### 7.1 Simulation with LiteX-Sim

LitePCIe can be simulated with Verilator using the existing test infrastructure:

```bash
# In litepcie directory
python3 -m unittest test.test_dma
```

### 7.2 TLP-Level Verification

Create a test that:
1. Instantiates BSA exerciser
2. Writes to control CSRs
3. Captures the TLP bytes at phy.sink
4. Verifies attr bits are correctly set

```python
def test_bsa_attr_bits():
    # Setup
    dut = LitePCIeBSAExerciser(endpoint, address_width=64)
    
    # Write CSRs to set no-snoop
    yield dut.dma_control.storage.eq(0b00100001)  # trigger + no_snoop
    yield dut.dma_address.storage.eq(0x1234_5678)
    
    # Wait for TLP generation
    yield from wait_for(dut.port.source.valid)
    
    # Capture at packetizer output and verify
    # attr bits should be 0b01 (NS=1, RO=0)
```

### 7.3 Hardware Testing on x86

As planned, ~80% of functionality can be tested on an x86 system:
1. Build LitePCIe with BSA exerciser for supported x86 FPGA board
2. Load driver and verify CSR access
3. Run DMA tests and capture TLP traces
4. Verify attribute bits in hardware analyzer or driver logs

---

## Appendix A: Data Flow Diagram

```
                          BSA Exerciser Module (NEW)
                                    │
                                    │ request_layout + attr, at
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         Crossbar                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐                        │
│  │ DMA Port │   │ BSA Port │   │ MSI Port │  ... other ports       │
│  └────┬─────┘   └────┬─────┘   └────┬─────┘                        │
│       │              │              │                               │
│       └──────────────┴──────────────┘                               │
│                      │                                              │
│                      │ Arbitrate/Dispatch                           │
│                      ▼                                              │
│              ┌──────────────┐                                       │
│              │  Controller  │ (manages pending requests)            │
│              └──────┬───────┘                                       │
└─────────────────────┼───────────────────────────────────────────────┘
                      │
                      ▼ request_layout
              ┌──────────────┐
              │  Packetizer  │  ◄─── MODIFY: tlp_req.attr.eq(req_sink.attr)
              │              │
              │  - Format    │
              │  - Encode    │
              │  - Insert    │
              └──────┬───────┘
                     │
                     ▼ phy_layout (dat, be)
              ┌──────────────┐
              │     PHY      │  (to PCIe link)
              └──────────────┘
```

---

## Appendix B: PCIe TLP Header Reference

```
DW0 (Bits 31:0):
┌─────┬──────┬────┬────┬────┬────┬───────┬──────────┐
│ Fmt │ Type │ R  │ TC │ R  │Attr│  R    │  Length  │
│31:29│28:24 │ 23 │22:20│19:16│15:12│11:10  │  9:0     │
└─────┴──────┴────┴────┴────┴────┴───────┴──────────┘

Attr field (bits 14:12 in DW0):
  - Bit 12 (attr[0]): No Snoop (NS)
  - Bit 13 (attr[1]): Relaxed Ordering (RO)
  - Bit 14 (attr[2]): ID-based Ordering (IDO)

Note: LitePCIe header definition shows attr at offset 12, width 2
This covers NS and RO. IDO would need separate handling.
```

---

## Conclusion

The LitePCIe architecture is well-suited for BSA exerciser extension. The primary modification for attribute control is straightforward (single line change + layout extension). PASID and ATS support are more involved but follow clear patterns established by PTM.

**Recommended Next Steps:**
1. Implement Phase 1 (attr field extension) as a proof of concept
2. Create basic BSA exerciser frontend
3. Test on x86 FPGA board
4. Add PASID support based on target PHY capabilities
5. Add ATS support for full SMMU testing
