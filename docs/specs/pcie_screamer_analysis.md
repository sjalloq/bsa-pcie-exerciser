# PCIe Screamer LiteX Port Analysis

## Overview

The `enjoy-digital/pcie_screamer` repository provides a LiteX/Migen-based gateware for the PCIe Screamer hardware (Artix7 XC7A35T + FT601 USB 3.0). This is directly relevant to our BSA exerciser as it demonstrates proven FT601 integration with LitePCIe.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         pcie_screamer.py                            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────┐      ┌──────────────┐      ┌──────────────┐      │
│  │  S7PCIEPHY   │      │   USBCore    │      │  FT601Sync   │      │
│  │  (LitePCIe)  │      │  (channels)  │      │   (PHY)      │      │
│  └──────┬───────┘      └──────┬───────┘      └──────┬───────┘      │
│         │                     │                     │               │
│         │    ┌────────────────┴────────────────┐    │               │
│         │    │                                 │    │               │
│         │    │   ┌─────────┐   ┌───────────┐  │    │               │
│         ├────┼──►│   TLP   │   │ Etherbone │  │    │               │
│         │    │   │Sender/  │   │(Wishbone) │  │    │               │
│         │    │   │Receiver │   └───────────┘  │    │               │
│         │    │   └─────────┘                  │    │               │
│         │    │                                │    │               │
│         │    └────────────────────────────────┘    │               │
│         │                                          │               │
└─────────┴──────────────────────────────────────────┴───────────────┘
```

## Key Modules

### 1. FT601Sync (`gateware/ft601.py`)

Low-level FT601 synchronous FIFO interface.

```python
class FT601Sync(Module):
    def __init__(self, pads, dw=32, timeout=1024):
        # Async FIFOs for clock domain crossing
        read_fifo = ClockDomainsRenamer({"write": "usb", "read": "sys"})(
            stream.AsyncFIFO(phy_description(dw), 128))
        write_fifo = ClockDomainsRenamer({"write": "sys", "read": "usb"})(
            stream.AsyncFIFO(phy_description(dw), 128))
        
        # Exposes:
        self.source = stream.Endpoint(...)  # RX from USB host
        self.sink = stream.Endpoint(...)    # TX to USB host
```

**Key Features:**
- 32-bit data width matching FT601 bus
- AsyncFIFO handles USB (100MHz) ↔ system clock domain crossing
- Simple stream interface for TX/RX
- FSM handles FT601 control signals (rxf_n, txe_n, oe_n, rd_n, wr_n)

### 2. USBCore (`gateware/usb.py`)

Multiplexes multiple logical channels over the single USB PHY.

```python
# Channel mapping from pcie_screamer.py:
self.usb_map = {
    "wishbone": 0,
    "tlp": 1,
}

self.submodules.usb_core = USBCore(self.usb_phy, self.usb_map)
```

**Purpose:**
- Provides named channels for different data streams
- Each channel gets independent source/sink endpoints
- Handles packetization/depacketization with channel IDs

### 3. TLPSender/TLPReceiver (`gateware/tlp.py`)

Converts PCIe TLP format to USB stream format.

```python
class TLPSender(Module):
    def __init__(self, identifier, fifo_depth=512):
        self.sink = stream.Endpoint(tlp_description(64))    # From PCIe PHY
        self.source = stream.Endpoint(usb_description(32))  # To USB
        
        # Pipeline:
        # 1. Buffer incoming TLPs
        # 2. StrideConverter: 64-bit → 32-bit
        # 3. FIFO for depth
        # 4. Add header with identifier
```

**Key Insight:** TLP data is width-converted and prefixed with an identifier byte for channel routing.

### 4. Etherbone (`gateware/etherbone.py`)

Wishbone bridge over USB for register access.

```python
self.submodules.etherbone = Etherbone(self.usb_core, self.usb_map["wishbone"])
self.bus.add_master(master=self.etherbone.master.bus)
```

**Purpose:** Allows host PC to access CSRs via USB packets.

## Clock Domains

```python
class _CRG(Module):
    def __init__(self, platform, sys_clk_freq):
        self.clock_domains.cd_sys = ClockDomain()   # System (125MHz typical)
        self.clock_domains.cd_usb = ClockDomain()   # FT601 clock (100MHz)
        
        # USB clock comes directly from FT601
        usb_clk100 = platform.request("usb_fifo_clock")
        self.comb += self.cd_usb.clk.eq(usb_clk100)
```

## Data Flow

### PCIe → USB (TLP Capture)
```
PCIe PHY source → TLPSender.sink → 64→32 converter → FIFO → USBCore → FT601 → Host
```

### USB → PCIe (TLP Injection)
```
Host → FT601 → USBCore → TLPReceiver.source → 32→64 converter → PCIe PHY sink
```

## Relevance to BSA Exerciser USB Monitoring

### What We Can Reuse

1. **FT601Sync module** - Direct reuse, proven working with CaptainDMA hardware
2. **Clock domain crossing pattern** - AsyncFIFO between sys and usb domains
3. **Stream-based architecture** - Clean integration with LiteX streams
4. **Etherbone for control** - CSR access over USB

### What We Need to Adapt

1. **Packet Format** - Our monitoring packets need different structure:
   - 32-byte header (timestamp, type, flags, etc.)
   - Variable payload with TLP data + attributes
   - BSA-specific fields (bar_hit, first_be, last_be, attr, at)

2. **Data Source** - We're capturing from our multi-BAR infrastructure, not raw PHY:
   - Tap points in BAR dispatcher
   - DMA engine monitoring
   - MSI-X event capture

3. **Channel Architecture** - Simpler for monitoring:
   - Channel 0: Monitor data (TX only, FPGA → Host)
   - Channel 1: Control/status (bidirectional)

## Proposed Integration

```python
class BSAMonitorUSB(LiteXModule):
    def __init__(self, platform, capture_sources):
        # FT601 PHY (reuse from pcie_screamer)
        self.submodules.ft601 = FT601Sync(
            platform.request("usb_fifo"), 
            dw=32, 
            timeout=1024
        )
        
        # Capture merger (arbiter for multiple tap points)
        self.submodules.merger = stream.Arbiter(
            [src.capture for src in capture_sources],
            stream.Endpoint(capture_layout)
        )
        
        # Packet formatter (our custom format)
        self.submodules.formatter = BSAPacketFormatter()
        
        # Async FIFO for CDC
        self.submodules.fifo = ClockDomainsRenamer(
            {"write": "sys", "read": "usb"}
        )(stream.AsyncFIFO(usb_layout(32), 4096))
        
        # Connect pipeline
        self.comb += [
            self.merger.source.connect(self.formatter.sink),
            self.formatter.source.connect(self.fifo.sink),
            self.fifo.source.connect(self.ft601.sink),
        ]
```

## Host Software Reference

The pcie_screamer includes:
- `drivers/ft60x/` - C driver using libftd3xx
- `software/tlp.py` - Python TLP parsing

We can adapt these for our monitoring protocol with:
- Python `ftd3xx` bindings (or `pyftdi` for cross-platform)
- Rich-based live display
- pcap export for Wireshark

## Next Steps

1. **Copy FT601Sync** - Extract from pcie_screamer or lambdaconcept/usbsniffer
2. **Design capture layout** - Define Migen record for captured transactions
3. **Implement BSAPacketFormatter** - Convert captures to USB packets
4. **Add tap points** - Non-intrusive captures in bar_routing, dma, msix
5. **Write host receiver** - Python tool to decode and display

## References

- https://github.com/enjoy-digital/pcie_screamer
- https://github.com/lambdaconcept/usbsniffer (similar FT601 use)
- https://github.com/ufrisk/pcileech-fpga (production FT601 firmware)
- FTDI FT601 Datasheet (AN_370, AN_421)
