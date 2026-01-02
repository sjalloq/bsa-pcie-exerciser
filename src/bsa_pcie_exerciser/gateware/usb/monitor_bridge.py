#
# Monitor Bridge - Transaction Monitor to USB Streaming
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Bridges the PCIe transaction monitor to USB streaming channel.
# Formats captured transactions into USB packets with header + payload.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from .core import usb_channel_description


# =============================================================================
# Packet Constants
# =============================================================================

# Magic number for BSA monitor packets ("BSAX")
MONITOR_MAGIC = 0x42534158

# Packet types
PKT_MEM_RD_REQ  = 0x0001  # Memory Read Request (inbound)
PKT_MEM_WR_REQ  = 0x0002  # Memory Write Request (inbound)
PKT_COMPLETION  = 0x0003  # Completion (either direction)
PKT_MSIX_WR     = 0x0004  # MSI-X Memory Write (outbound)
PKT_DMA_RD      = 0x0005  # DMA Read Request (outbound)
PKT_DMA_WR      = 0x0006  # DMA Write Request (outbound)
PKT_ATS_REQ     = 0x0007  # ATS Translation Request (outbound)
PKT_ATS_CPL     = 0x0008  # ATS Translation Completion (inbound)
PKT_OVERFLOW    = 0x00FF  # FIFO overflow marker
PKT_SYNC        = 0x0100  # Timestamp synchronization

# Header size in 32-bit words
HEADER_WORDS = 8  # 32 bytes / 4 = 8 words


# =============================================================================
# Monitor Bridge
# =============================================================================

class MonitorBridge(LiteXModule):
    """
    Bridges Transaction Monitor to USB streaming channel.

    Formats captured transactions into USB packets:
    - 32-byte header (magic, seq, timestamp, attributes)
    - Variable payload (address + data from TLP)
    - Padding to 32-byte boundary

    The bridge taps into the same signals as TransactionMonitor but
    streams them over USB instead of storing in a register-readable FIFO.

    Parameters
    ----------
    data_width : int
        PCIe data width (64, 128, etc.)

    Interfaces
    ----------
    tap_* : Signals
        Same tap interface as TransactionMonitor (directly connected)
    source : stream.Endpoint
        USB channel stream output (usb_channel_description)
    enable : Signal
        Enable streaming (from CSR)
    overflow : Signal
        Set when USB backpressure causes dropped transactions
    """

    def __init__(self, data_width=64):
        assert data_width >= 64, "Minimum 64-bit data width"

        self.data_width = data_width

        # =====================================================================
        # Tap Interface (same as TransactionMonitor)
        # =====================================================================

        self.tap_valid    = Signal()
        self.tap_first    = Signal()
        self.tap_last     = Signal()
        self.tap_we       = Signal()
        self.tap_adr      = Signal(64)
        self.tap_len      = Signal(10)
        self.tap_dat      = Signal(data_width)
        self.tap_first_be = Signal(4)
        self.tap_last_be  = Signal(4)
        self.tap_bar_hit  = Signal(6)
        self.tap_attr     = Signal(2)
        self.tap_at       = Signal(2)
        self.tap_req_id   = Signal(16)
        self.tap_tag      = Signal(8)

        # =====================================================================
        # Control/Status
        # =====================================================================

        self.enable   = Signal()  # Enable streaming
        self.overflow = Signal()  # Sticky overflow flag

        # =====================================================================
        # USB Stream Output
        # =====================================================================

        self.source = source = stream.Endpoint(usb_channel_description(32))

        # =====================================================================
        # Internal State
        # =====================================================================

        # Timestamp counter (free-running, ~8ns per tick at 125MHz)
        timestamp = Signal(64)
        self.sync += timestamp.eq(timestamp + 8)

        # Sequence number
        seq = Signal(32)

        # Overflow tracking
        overflow_reg = Signal()
        self.comb += self.overflow.eq(overflow_reg)

        # Captured transaction (latched on first beat)
        cap_we       = Signal()
        cap_adr      = Signal(64)
        cap_len      = Signal(10)
        cap_dat      = Signal(64)  # First 64 bits of data
        cap_first_be = Signal(4)
        cap_last_be  = Signal(4)
        cap_bar_hit  = Signal(6)
        cap_attr     = Signal(2)
        cap_at       = Signal(2)
        cap_req_id   = Signal(16)
        cap_tag      = Signal(8)
        cap_timestamp = Signal(64)

        # Header words (built from captured state)
        header = Array([Signal(32, name=f"header_{i}") for i in range(HEADER_WORDS)])

        # Word 0: Magic
        self.comb += header[0].eq(MONITOR_MAGIC)

        # Word 1: Sequence number
        self.comb += header[1].eq(seq)

        # Word 2-3: Timestamp (64-bit)
        self.comb += [
            header[2].eq(cap_timestamp[:32]),
            header[3].eq(cap_timestamp[32:64]),
        ]

        # Word 4: Packet type (16) | Direction (8) | BAR hit (8)
        pkt_type = Signal(16)
        self.comb += [
            # Determine packet type based on direction and type
            If(cap_we,
                pkt_type.eq(PKT_MEM_WR_REQ),
            ).Else(
                pkt_type.eq(PKT_MEM_RD_REQ),
            ),
            header[4].eq(Cat(
                cap_bar_hit[:8],   # [7:0] BAR hit
                Constant(0, 8),    # [15:8] Direction (0=inbound)
                pkt_type,          # [31:16] Packet type
            )),
        ]

        # Word 5: First BE (8) | Last BE (8) | Attributes (8) | Reserved (8)
        self.comb += header[5].eq(Cat(
            Constant(0, 8),        # [7:0] Reserved
            Cat(cap_attr, cap_at, Constant(0, 4)),  # [15:8] Attributes
            cap_last_be,           # [23:16] Last BE
            cap_first_be,          # [31:24] First BE
        ))

        # Word 6: Length (16) | Requester ID (16)
        payload_len = Signal(16)
        self.comb += [
            # Payload = 8 bytes (address) + up to 8 bytes (data)
            payload_len.eq(16),  # Fixed for now: addr(8) + data(8)
            header[6].eq(Cat(
                cap_req_id,    # [15:0] Requester ID
                payload_len,   # [31:16] Payload length
            )),
        ]

        # Word 7: Tag (8) | Reserved (24)
        self.comb += header[7].eq(Cat(
            Constant(0, 24),   # [23:0] Reserved
            cap_tag,           # [31:24] Tag
        ))

        # Payload words (address + data)
        payload = Array([Signal(32, name=f"payload_{i}") for i in range(4)])
        self.comb += [
            payload[0].eq(cap_adr[:32]),      # Address[31:0]
            payload[1].eq(cap_adr[32:64]),    # Address[63:32]
            payload[2].eq(cap_dat[:32]),      # Data[31:0]
            payload[3].eq(cap_dat[32:64]),    # Data[63:32]
        ]

        # Total packet: 8 header words + 4 payload words = 12 words = 48 bytes
        TOTAL_WORDS = HEADER_WORDS + 4
        PACKET_BYTES = TOTAL_WORDS * 4

        # Word counter
        word_idx = Signal(max=TOTAL_WORDS)

        # =====================================================================
        # State Machine
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # Detect new transaction
        txn_arriving = Signal()
        self.comb += txn_arriving.eq(
            self.enable & self.tap_valid & self.tap_first
        )

        fsm.act("IDLE",
            source.valid.eq(0),
            If(txn_arriving,
                # Capture transaction on first beat
                NextValue(cap_we, self.tap_we),
                NextValue(cap_adr, self.tap_adr),
                NextValue(cap_len, self.tap_len),
                NextValue(cap_dat, self.tap_dat[:64]),
                NextValue(cap_first_be, self.tap_first_be),
                NextValue(cap_last_be, self.tap_last_be),
                NextValue(cap_bar_hit, self.tap_bar_hit),
                NextValue(cap_attr, self.tap_attr),
                NextValue(cap_at, self.tap_at),
                NextValue(cap_req_id, self.tap_req_id),
                NextValue(cap_tag, self.tap_tag),
                NextValue(cap_timestamp, timestamp),
                NextValue(word_idx, 0),
                NextState("SEND_HEADER"),
            ),
        )

        fsm.act("SEND_HEADER",
            source.valid.eq(1),
            source.data.eq(header[word_idx]),
            source.last.eq(0),
            source.dst.eq(1),  # Channel 1 = monitor
            source.length.eq(PACKET_BYTES),
            If(source.ready,
                NextValue(word_idx, word_idx + 1),
                If(word_idx == HEADER_WORDS - 1,
                    NextValue(word_idx, 0),
                    NextState("SEND_PAYLOAD"),
                ),
            ),
        )

        fsm.act("SEND_PAYLOAD",
            source.valid.eq(1),
            source.data.eq(payload[word_idx]),
            source.last.eq(word_idx == 3),
            source.dst.eq(1),
            source.length.eq(PACKET_BYTES),
            If(source.ready,
                NextValue(word_idx, word_idx + 1),
                If(word_idx == 3,
                    NextValue(seq, seq + 1),
                    NextState("IDLE"),
                ),
            ),
        )

        # Overflow detection: new transaction while still sending previous
        self.sync += [
            If(~self.enable,
                overflow_reg.eq(0),
            ).Elif(txn_arriving & ~fsm.ongoing("IDLE"),
                overflow_reg.eq(1),
            ),
        ]
