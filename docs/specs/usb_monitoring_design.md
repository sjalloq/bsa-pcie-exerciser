# BSA PCIe Exerciser - USB Monitoring Subsystem

**Design Document**

## 1. Overview

The CaptainDMA 4.1th board includes an FTDI FT601 USB 3.0 FIFO interface alongside the PCIe connection. This document specifies a monitoring subsystem that captures PCIe transactions and streams them to a host PC via USB for real-time analysis.

### 1.1 Goals

- Capture all inbound (host → endpoint) memory transactions
- Capture all outbound (endpoint → host) DMA/MSI transactions
- Stream captured data off-chip via USB 3.0 at wire speed
- Provide host software for live viewing, logging, and analysis
- Minimal impact on exerciser timing/functionality

### 1.2 Hardware Platform

- **FPGA:** Xilinx Artix-7 XC7A35T
- **USB Interface:** FTDI FT601Q (USB 3.0 SuperSpeed FIFO)
- **FT601 Mode:** 245 Synchronous FIFO, 32-bit wide, 100MHz

---

## 2. Architecture

```
                          ┌─────────────────────────────────────────────┐
                          │              BSA Exerciser FPGA             │
                          │                                             │
  PCIe                    │  ┌─────────┐    ┌──────────────┐           │
  ════════════════════════╪══│ PCIe    │═══▶│ Transaction  │           │
  Host                    │  │ PHY     │    │ Monitor Tap  │           │
                          │  └─────────┘    └──────┬───────┘           │
                          │        │               │                    │
                          │        ▼               ▼                    │
                          │  ┌─────────┐    ┌──────────────┐           │
                          │  │ Multi-  │    │   Capture    │           │
                          │  │ BAR     │    │   FIFO       │           │
                          │  │ Endpoint│    │  (4K deep)   │           │
                          │  └─────────┘    └──────┬───────┘           │
                          │        │               │                    │
                          │        ▼               ▼                    │
                          │  ┌─────────┐    ┌──────────────┐           │
                          │  │ DMA     │───▶│   Packet     │           │  USB 3.0
                          │  │ Engine  │    │   Formatter  │═══════════╪════════▶ Host PC
                          │  └─────────┘    └──────┬───────┘           │
                          │        │               │                    │
                          │        ▼               ▼                    │
                          │  ┌─────────┐    ┌──────────────┐           │
                          │  │ MSI-X   │───▶│   FT601      │           │
                          │  │ Ctrl    │    │   Interface  │           │
                          │  └─────────┘    └──────────────┘           │
                          │                                             │
                          └─────────────────────────────────────────────┘
```

### 2.1 Capture Points

| Point | Direction | What's Captured |
|-------|-----------|-----------------|
| PHY RX | Inbound | All TLPs from host (MRd, MWr, Cpl) |
| PHY TX | Outbound | All TLPs to host (DMA, MSI, Cpl) |
| BAR Dispatch | Inbound | Decoded requests with BAR routing |
| DMA Engine | Outbound | DMA read/write requests |
| MSI-X Controller | Outbound | MSI memory writes |

### 2.2 Capture Modes

1. **Full Capture** - Every TLP (high bandwidth, may overflow)
2. **Filtered** - Only specific BAR, address range, or TLP type
3. **Triggered** - Start/stop on pattern match
4. **Sampled** - Every Nth transaction (for long-term monitoring)

---

## 3. USB Packet Format

### 3.1 Packet Structure

All packets are 32-byte aligned for efficient USB transfer.

```
Offset  Size   Field
──────  ────   ─────────────────────────────────
0x00    4      Magic + Version (0x50434945 = "PCIE")
0x04    4      Sequence Number
0x08    8      Timestamp (64-bit, ns resolution)
0x10    2      Packet Type
0x12    2      Flags
0x14    2      Length (payload bytes following header)
0x16    2      Reserved
0x18    8      Reserved (pad to 32 bytes)
──────────────────────────────────────────────────
0x20    N      Payload (type-specific, 0-224 bytes)
──────────────────────────────────────────────────
        32+N   Total (padded to 32-byte boundary)
```

### 3.2 Packet Types

| Type | Value | Description |
|------|-------|-------------|
| TXN_INBOUND_REQ | 0x0001 | Inbound memory read/write request |
| TXN_INBOUND_CPL | 0x0002 | Inbound completion (to DMA engine) |
| TXN_OUTBOUND_REQ | 0x0003 | Outbound DMA read/write request |
| TXN_OUTBOUND_CPL | 0x0004 | Outbound completion (from BAR handler) |
| TXN_MSI | 0x0005 | MSI/MSI-X memory write |
| CTRL_OVERFLOW | 0x0100 | FIFO overflow notification |
| CTRL_SYNC | 0x0101 | Sync marker (for alignment) |
| CTRL_TIMESTAMP | 0x0102 | Timestamp calibration |
| CTRL_CONFIG | 0x0103 | Configuration change |

### 3.3 Flags Field

| Bit | Name | Description |
|-----|------|-------------|
| 0 | WRITE | 1 = Write, 0 = Read |
| 1 | HAS_DATA | Payload contains data |
| 2 | TRUNCATED | Data was truncated |
| 3 | ERROR | Error condition |
| 4 | NO_SNOOP | No Snoop attribute set |
| 5 | RELAXED_ORD | Relaxed Ordering set |
| 7:6 | ADDR_TYPE | AT field (00/01/10/11) |
| 10:8 | BAR_HIT | Which BAR (0-5) |
| 15:11 | Reserved | - |

### 3.4 TXN_INBOUND_REQ Payload

Captures inbound memory requests from host.

```
Offset  Size   Field
──────  ────   ─────────────────────────────────
0x00    8      Address (64-bit)
0x08    4      Length (DWORDs)
0x0C    2      Requester ID
0x0E    1      Tag
0x0F    1      First BE (4 bits) | Last BE (4 bits)
0x10    2      Attributes (TC, Attr, AT)
0x12    2      Reserved
0x14    N      Data (for writes, up to 128 bytes)
```

### 3.5 TXN_OUTBOUND_REQ Payload

Captures outbound DMA requests.

```
Offset  Size   Field
──────  ────   ─────────────────────────────────
0x00    8      Address (64-bit)
0x08    4      Length (DWORDs)
0x0C    2      Requester ID
0x0E    1      Tag
0x0F    1      First BE | Last BE
0x10    2      Attributes
0x12    2      Reserved
0x14    N      Data (for writes, up to 128 bytes)
```

### 3.6 TXN_MSI Payload

Captures MSI-X interrupts.

```
Offset  Size   Field
──────  ────   ─────────────────────────────────
0x00    8      Message Address
0x08    4      Message Data
0x0C    2      Vector Number
0x0E    2      Reserved
```

### 3.7 CTRL_OVERFLOW Payload

```
Offset  Size   Field
──────  ────   ─────────────────────────────────
0x00    4      Dropped packet count
0x04    4      FIFO high watermark
```

---

## 4. FPGA Implementation

### 4.1 Module: TransactionMonitorTap

Taps into PHY streams without disrupting normal operation.

```python
class TransactionMonitorTap(LiteXModule):
    """
    Non-intrusive tap on PHY RX/TX streams.
    
    Creates copy of transactions for capture without
    affecting timing of main data path.
    """
    
    def __init__(self, phy_source, phy_sink):
        # Capture interface (active when capture enabled)
        self.rx_capture = stream.Endpoint(phy_layout(64))
        self.tx_capture = stream.Endpoint(phy_layout(64))
        
        # Control
        self.enable = Signal()
        self.rx_enable = Signal()
        self.tx_enable = Signal()
        
        # Statistics
        self.rx_count = Signal(32)
        self.tx_count = Signal(32)
```

### 4.2 Module: CaptureFormatter

Converts internal transaction format to USB packet format.

```python
class CaptureFormatter(LiteXModule):
    """
    Formats captured transactions into USB packets.
    
    - Adds header with timestamp and sequence number
    - Handles data truncation for large payloads
    - Pads to 32-byte boundary
    """
    
    def __init__(self, data_width=64):
        # Input from monitor tap
        self.sink = stream.Endpoint(capture_layout(data_width))
        
        # Output to FIFO
        self.source = stream.Endpoint([("data", 32)])
        
        # Timestamp counter (free-running, ns resolution)
        self.timestamp = Signal(64)
        
        # Sequence number
        self.seq_num = Signal(32)
```

### 4.3 Module: CaptureFIFO

Async FIFO bridging PCIe clock domain to USB clock domain.

```python
class CaptureFIFO(LiteXModule):
    """
    Async FIFO for clock domain crossing.
    
    - PCIe domain: 125MHz (or 250MHz)
    - USB domain: 100MHz (FT601 clock)
    - Depth: 4096 x 32-bit words (16KB)
    """
    
    def __init__(self):
        # Write side (PCIe clock)
        self.sink = stream.Endpoint([("data", 32)])
        
        # Read side (USB clock)
        self.source = stream.Endpoint([("data", 32)])
        
        # Status
        self.overflow = Signal()
        self.level = Signal(12)  # 0-4095
```

### 4.4 Module: FT601Interface

Interface to FTDI FT601 USB 3.0 FIFO IC.

```python
class FT601Interface(LiteXModule):
    """
    FT601 Synchronous FIFO interface.
    
    Directly connects to FT601 pins in 245 sync mode.
    32-bit data width, active-low control signals.
    """
    
    def __init__(self, pads):
        # Stream interface
        self.sink = stream.Endpoint([("data", 32)])   # TX to host
        self.source = stream.Endpoint([("data", 32)]) # RX from host
        
        # FT601 signals
        # pads.data[31:0]  - Bidirectional data
        # pads.be[3:0]     - Byte enable
        # pads.rxf_n       - RX FIFO not empty (can read)
        # pads.txe_n       - TX FIFO not full (can write)
        # pads.rd_n        - Read strobe
        # pads.wr_n        - Write strobe
        # pads.oe_n        - Output enable
        # pads.clk         - 100MHz clock from FT601
```

### 4.5 Integration

```python
class USBMonitor(LiteXModule):
    """
    Complete USB monitoring subsystem.
    """
    
    def __init__(self, phy, endpoint, ft601_pads):
        # Tap points
        self.phy_tap = TransactionMonitorTap(phy.source, phy.sink)
        self.dma_tap = ...  # Tap DMA engine
        self.msix_tap = ... # Tap MSI-X controller
        
        # Merge all capture sources
        self.merger = stream.Arbiter([...], ...)
        
        # Format packets
        self.formatter = CaptureFormatter()
        
        # Clock domain crossing
        self.fifo = CaptureFIFO()
        
        # USB interface
        self.ft601 = FT601Interface(ft601_pads)
        
        # Connect pipeline
        self.comb += [
            self.merger.source.connect(self.formatter.sink),
            self.formatter.source.connect(self.fifo.sink),
            self.fifo.source.connect(self.ft601.sink),
        ]
        
        # Control CSRs (accessible via BAR0)
        self.control = CSRStorage(32, fields=[
            CSRField("enable",     size=1, offset=0),
            CSRField("rx_enable",  size=1, offset=1),
            CSRField("tx_enable",  size=1, offset=2),
            CSRField("dma_enable", size=1, offset=3),
            CSRField("msi_enable", size=1, offset=4),
        ])
        
        self.status = CSRStatus(32, fields=[
            CSRField("fifo_level",  size=12, offset=0),
            CSRField("overflow",    size=1,  offset=12),
            CSRField("usb_ready",   size=1,  offset=13),
        ])
```

---

## 5. Host Software

### 5.1 Python Library: `bsa_monitor`

```python
# bsa_monitor/capture.py

import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterator, Optional
import usb.core

class PacketType(IntEnum):
    TXN_INBOUND_REQ  = 0x0001
    TXN_INBOUND_CPL  = 0x0002
    TXN_OUTBOUND_REQ = 0x0003
    TXN_OUTBOUND_CPL = 0x0004
    TXN_MSI          = 0x0005
    CTRL_OVERFLOW    = 0x0100
    CTRL_SYNC        = 0x0101

@dataclass
class PCIeTransaction:
    """Decoded PCIe transaction."""
    timestamp_ns: int
    seq_num: int
    pkt_type: PacketType
    direction: str  # "inbound" or "outbound"
    is_write: bool
    address: int
    length: int
    requester_id: int
    tag: int
    first_be: int
    last_be: int
    bar_hit: int
    no_snoop: bool
    relaxed_ordering: bool
    addr_type: int
    data: Optional[bytes]


class CaptureDevice:
    """Interface to BSA Exerciser USB capture."""
    
    VENDOR_ID = 0x0403   # FTDI
    PRODUCT_ID = 0x601f  # FT601
    
    def __init__(self):
        self.dev = usb.core.find(
            idVendor=self.VENDOR_ID,
            idProduct=self.PRODUCT_ID
        )
        if self.dev is None:
            raise RuntimeError("CaptainDMA not found")
        self.dev.set_configuration()
        
    def read_packets(self) -> Iterator[PCIeTransaction]:
        """Yield decoded transactions from USB."""
        while True:
            data = self.dev.read(0x81, 32 * 64, timeout=1000)
            yield from self._decode_buffer(data)
    
    def _decode_buffer(self, data: bytes) -> Iterator[PCIeTransaction]:
        """Decode USB buffer into transactions."""
        offset = 0
        while offset + 32 <= len(data):
            # Parse header
            magic, seq, ts_lo, ts_hi, ptype, flags, length, _ = struct.unpack_from(
                "<IIQHHHHQ", data, offset
            )
            
            if magic != 0x50434945:  # "PCIE"
                offset += 32
                continue
            
            timestamp = ts_lo | (ts_hi << 32)
            payload = data[offset+32 : offset+32+length]
            
            txn = self._decode_transaction(
                seq, timestamp, ptype, flags, payload
            )
            if txn:
                yield txn
            
            # Advance to next packet (32-byte aligned)
            offset += 32 + ((length + 31) & ~31)
    
    def _decode_transaction(self, seq, timestamp, ptype, flags, payload):
        """Decode specific transaction type."""
        if ptype == PacketType.TXN_INBOUND_REQ:
            addr, length, req_id, tag, be, attr, _ = struct.unpack_from(
                "<QIHBBHH", payload
            )
            first_be = be & 0xF
            last_be = (be >> 4) & 0xF
            
            data = payload[0x14:] if (flags & 0x02) else None
            
            return PCIeTransaction(
                timestamp_ns=timestamp,
                seq_num=seq,
                pkt_type=PacketType(ptype),
                direction="inbound",
                is_write=bool(flags & 0x01),
                address=addr,
                length=length,
                requester_id=req_id,
                tag=tag,
                first_be=first_be,
                last_be=last_be,
                bar_hit=(flags >> 8) & 0x07,
                no_snoop=bool(flags & 0x10),
                relaxed_ordering=bool(flags & 0x20),
                addr_type=(flags >> 6) & 0x03,
                data=data,
            )
        # ... other packet types ...
        return None
```

### 5.2 CLI Tool: `pcie-monitor`

```python
#!/usr/bin/env python3
# bsa_monitor/cli.py

import click
from rich.console import Console
from rich.table import Table
from rich.live import Live

from .capture import CaptureDevice, PacketType

console = Console()

@click.group()
def cli():
    """BSA PCIe Exerciser Monitor"""
    pass

@cli.command()
@click.option("--filter-bar", type=int, help="Only show specific BAR")
@click.option("--filter-write/--filter-read", default=None)
@click.option("--hex-dump/--no-hex-dump", default=False)
def live(filter_bar, filter_write, hex_dump):
    """Live capture display."""
    dev = CaptureDevice()
    
    table = Table(title="PCIe Transactions")
    table.add_column("Time", style="cyan")
    table.add_column("Dir", style="magenta")
    table.add_column("Type", style="green")
    table.add_column("BAR")
    table.add_column("Address", style="yellow")
    table.add_column("Len")
    table.add_column("BE")
    table.add_column("Data")
    
    with Live(table, refresh_per_second=10) as live:
        for txn in dev.read_packets():
            # Apply filters
            if filter_bar is not None and txn.bar_hit != filter_bar:
                continue
            if filter_write is not None and txn.is_write != filter_write:
                continue
            
            # Format BE
            be_str = f"{txn.first_be:X}/{txn.last_be:X}"
            
            # Format data
            if txn.data and hex_dump:
                data_str = txn.data[:16].hex()
                if len(txn.data) > 16:
                    data_str += "..."
            else:
                data_str = f"{len(txn.data)} bytes" if txn.data else "-"
            
            table.add_row(
                f"{txn.timestamp_ns/1e6:.3f}",
                txn.direction[:2].upper(),
                "WR" if txn.is_write else "RD",
                str(txn.bar_hit),
                f"0x{txn.address:08X}",
                str(txn.length),
                be_str,
                data_str,
            )
            
            # Keep table size reasonable
            if len(table.rows) > 100:
                table.rows.pop(0)

@cli.command()
@click.argument("output", type=click.File("wb"))
@click.option("--duration", type=float, help="Capture duration in seconds")
def capture(output, duration):
    """Capture to file."""
    import time
    dev = CaptureDevice()
    
    start = time.time()
    count = 0
    
    console.print(f"Capturing to {output.name}...")
    
    try:
        for txn in dev.read_packets():
            # Write raw packet data
            output.write(txn.raw_bytes)
            count += 1
            
            if duration and (time.time() - start) >= duration:
                break
    except KeyboardInterrupt:
        pass
    
    console.print(f"Captured {count} transactions")

@cli.command()
@click.argument("input", type=click.File("rb"))
def analyze(input):
    """Analyze capture file."""
    # ... statistics, patterns, etc.
    pass

if __name__ == "__main__":
    cli()
```

### 5.3 Wireshark Dissector (Future)

A Wireshark dissector could be written to visualize captures in Wireshark's familiar interface. This would use the extcap interface to read from USB in real-time.

---

## 6. BAR0 Register Extensions

Add to BAR0 register map for monitor control:

| Offset | Name | Description |
|--------|------|-------------|
| 0x080 | MON_CTRL | Monitor control |
| 0x084 | MON_STATUS | Monitor status |
| 0x088 | MON_FILTER_BAR | BAR filter mask |
| 0x08C | MON_FILTER_ADDR_LO | Address filter low |
| 0x090 | MON_FILTER_ADDR_HI | Address filter high |
| 0x094 | MON_FILTER_MASK | Address mask |
| 0x098 | MON_PKT_COUNT | Packet counter |
| 0x09C | MON_DROP_COUNT | Dropped packet counter |

**MON_CTRL Register:**
| Bits | Field | Description |
|------|-------|-------------|
| 0 | ENABLE | Global enable |
| 1 | RX_EN | Capture inbound |
| 2 | TX_EN | Capture outbound |
| 3 | DMA_EN | Capture DMA |
| 4 | MSI_EN | Capture MSI |
| 5 | FILTER_EN | Enable address filter |
| 6 | TRIGGERED | Trigger mode |
| 7 | OVERFLOW_STOP | Stop on overflow |

---

## 7. Implementation Phases

### Phase 1: Basic USB Streaming
- FT601 interface module
- Simple test pattern generator
- Verify USB communication with host
- Basic Python receiver

### Phase 2: Transaction Capture
- PHY tap module
- Packet formatter (header + basic payload)
- Async FIFO
- Live CLI tool

### Phase 3: Full Feature Set
- All capture points (DMA, MSI, etc.)
- Filtering and triggering
- Statistics and counters
- Capture file format

### Phase 4: Analysis Tools
- Wireshark dissector
- Pattern detection
- Performance analysis
- BSA test correlation

---

## 8. File Structure

```
src/bsa_pcie_exerciser/
├── monitor/
│   ├── __init__.py
│   ├── txn_monitor.py      # Internal TXN_TRACE monitor
│   ├── usb_monitor.py      # USB streaming monitor
│   ├── capture_tap.py      # PHY/DMA/MSI tap modules
│   ├── packet_format.py    # Packet formatter
│   └── ft601.py            # FT601 interface
├── ...

tools/
├── bsa_monitor/
│   ├── __init__.py
│   ├── capture.py          # USB capture library
│   ├── decode.py           # Packet decoder
│   ├── cli.py              # CLI tool
│   └── wireshark/          # Wireshark dissector
└── setup.py
```

---

## 9. References

- FTDI FT601 Datasheet
- USB 3.0 Specification
- LiteX FT601 examples
- PCILeech firmware (for FT601 reference)
