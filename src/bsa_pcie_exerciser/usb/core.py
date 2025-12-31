#
# USB Core - Channel Multiplexing over FT601
#
# Copyright (c) 2016 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Ported from enjoy-digital/pcie_screamer with adaptations for BSA Exerciser.
#

from collections import OrderedDict

from migen import *
from migen.genlib.misc import WaitTimer

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.interconnect.stream import EndpointDescription
from litex.soc.interconnect.packet import Arbiter, Dispatcher, Header, HeaderField


# =============================================================================
# Packet Definitions
# =============================================================================

# USB packet header format (12 bytes):
#   - preamble: 4 bytes (0x5aa55aa5) - sync pattern
#   - dst:      4 bytes - destination channel ID (only low 8 bits used)
#   - length:   4 bytes - payload length in bytes

packet_header_length = 12
packet_header_fields = {
    "preamble": HeaderField(0,  0, 32),
    "dst":      HeaderField(4,  0, 32),
    "length":   HeaderField(8,  0, 32),
}
packet_header = Header(
    packet_header_fields,
    packet_header_length,
    swap_field_bytes=True
)


def usb_phy_description(dw):
    """Raw PHY-level data (just data bits, no framing)."""
    payload_layout = [("data", dw)]
    return EndpointDescription(payload_layout)


def usb_channel_description(dw):
    """
    USB channel data description (after depacketization).

    This is what users of the USB crossbar see - data with channel routing
    information but no packet framing overhead.

    Fields:
        dst: Destination channel ID (8-bit, supports up to 256 channels)
        length: Payload length in bytes
        data: Payload data
        error: Error flags (one per byte)
    """
    param_layout = [
        ("dst",    8),   # Channel ID (low 8 bits of 32-bit header field)
        ("length", 32),  # Payload length in bytes
    ]
    payload_layout = [
        ("data",  dw),
        ("error", dw//8),
    ]
    return EndpointDescription(payload_layout, param_layout)


# =============================================================================
# Port Classes
# =============================================================================

class USBMasterPort:
    """Master port connecting packetizer/depacketizer to crossbar."""
    def __init__(self, dw):
        self.source = stream.Endpoint(usb_channel_description(dw))
        self.sink   = stream.Endpoint(usb_channel_description(dw))


class USBSlavePort:
    """Slave port for crossbar users (e.g., Etherbone)."""
    def __init__(self, dw, tag):
        self.sink   = stream.Endpoint(usb_channel_description(dw))
        self.source = stream.Endpoint(usb_channel_description(dw))
        self.tag = tag


class USBUserPort(USBSlavePort):
    """User-facing port from the crossbar."""
    def __init__(self, dw, tag):
        USBSlavePort.__init__(self, dw, tag)


# =============================================================================
# USB Packetizer
# =============================================================================

class USBPacketizer(LiteXModule):
    """
    Wraps channel data into USB packets with header.

    Packet format:
    - preamble: 4 bytes (0x5aa55aa5)
    - dst: 4 bytes (channel ID, zero-extended from 8 bits)
    - length: 4 bytes (payload length)
    - payload: variable length data
    """

    def __init__(self):
        self.sink   = sink   = stream.Endpoint(usb_channel_description(32))
        self.source = source = stream.Endpoint(usb_phy_description(32))

        # # #

        header = [
            0x5aa55aa5,   # preamble
            sink.dst,     # dst (8-bit, zero-extended to 32)
            sink.length,  # length
        ]

        header_unpack = stream.Unpack(len(header), usb_phy_description(32))
        self.submodules += header_unpack

        for i, word in enumerate(header):
            chunk = getattr(header_unpack.sink.payload, "chunk" + str(i))
            self.comb += chunk.data.eq(word)

        self.fsm = fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            If(sink.valid,
                NextState("INSERT_HEADER")
            )
        )

        fsm.act("INSERT_HEADER",
            header_unpack.sink.valid.eq(1),
            source.valid.eq(1),
            source.data.eq(header_unpack.source.data),
            header_unpack.source.ready.eq(source.ready),
            If(header_unpack.sink.ready,
                NextState("COPY")
            )
        )

        fsm.act("COPY",
            source.valid.eq(sink.valid),
            source.data.eq(sink.data),
            sink.ready.eq(source.ready),
            If(source.valid & source.ready & sink.last,
                NextState("IDLE")
            )
        )


# =============================================================================
# USB Depacketizer
# =============================================================================

class USBDepacketizer(LiteXModule):
    """
    Extracts channel data from USB packets.

    Looks for preamble (0x5aa55aa5), extracts header, passes payload.
    Includes timeout to recover from corrupted/incomplete packets.

    Args:
        clk_freq: System clock frequency in Hz
        timeout: Timeout in seconds for incomplete packets (default 10s)
    """

    def __init__(self, clk_freq, timeout=10):
        self.sink   = sink   = stream.Endpoint(usb_phy_description(32))
        self.source = source = stream.Endpoint(usb_channel_description(32))

        # # #

        preamble = Signal(32)

        # Header fields to extract (dst and length)
        header = [
            source.dst,
            source.length,
        ]

        header_pack = ResetInserter()(stream.Pack(usb_phy_description(32), len(header)))
        self.submodules += header_pack

        for i, field in enumerate(header):
            chunk = getattr(header_pack.source.payload, "chunk" + str(i))
            self.comb += field.eq(chunk.data)

        self.fsm = fsm = FSM(reset_state="IDLE")

        self.comb += preamble.eq(sink.data)

        fsm.act("IDLE",
            sink.ready.eq(1),
            header_pack.source.ready.eq(1),
            If((sink.data == 0x5aa55aa5) & sink.valid,
                NextState("RECEIVE_HEADER")
            ),
        )

        # Timeout for incomplete packets
        self.timer = WaitTimer(int(clk_freq * timeout))
        self.comb += self.timer.wait.eq(~fsm.ongoing("IDLE"))
        self.comb += header_pack.reset.eq(self.timer.done)

        fsm.act("RECEIVE_HEADER",
            header_pack.sink.valid.eq(sink.valid),
            header_pack.sink.payload.eq(sink.payload),
            If(self.timer.done,
                NextState("IDLE")
            ).Elif(header_pack.source.valid,
                NextState("COPY")
            ).Else(
                sink.ready.eq(1)
            )
        )

        last = Signal()
        cnt = Signal(32, reset_less=True)

        fsm.act("COPY",
            source.valid.eq(sink.valid),
            source.last.eq(last),
            source.data.eq(sink.data),
            sink.ready.eq(source.ready),
            If((source.valid & source.ready & last) | self.timer.done,
                NextState("IDLE")
            )
        )

        self.sync += \
            If(fsm.ongoing("IDLE"),
                cnt.eq(0)
            ).Elif(source.valid & source.ready,
                cnt.eq(cnt + 1)
            )

        # length is in bytes, convert to 32-bit words
        self.comb += last.eq(cnt == source.length[2:] - 1)


# =============================================================================
# USB Crossbar
# =============================================================================

class USBCrossbar(LiteXModule):
    """
    Routes USB packets between master port and multiple user ports.

    Each user port has a unique channel ID. The crossbar:
    - TX: Arbitrates between user ports sending to master
    - RX: Dispatches from master to user ports based on dst field
    """

    def __init__(self):
        self.users = OrderedDict()
        self.master = USBMasterPort(32)
        self.dispatch_param = "dst"

    def get_port(self, channel_id):
        """Get a user port for the specified channel ID."""
        port = USBUserPort(32, channel_id)
        if channel_id in self.users.keys():
            raise ValueError(f"Channel {channel_id:#x} already assigned")
        self.users[channel_id] = port
        return port

    def do_finalize(self):
        # TX: Arbitrate between user ports sending to master
        sinks = [port.sink for port in self.users.values()]
        self.arbiter = Arbiter(sinks, self.master.source)

        # RX: Dispatch from master to user ports based on dst
        sources = [port.source for port in self.users.values()]
        self.dispatcher = Dispatcher(
            self.master.sink,
            sources,
            one_hot=True
        )

        cases = {"default": self.dispatcher.sel.eq(0)}
        for i, (channel_id, _) in enumerate(self.users.items()):
            cases[channel_id] = self.dispatcher.sel.eq(2**i)

        self.comb += Case(
            getattr(self.master.sink, self.dispatch_param),
            cases
        )


# =============================================================================
# USB Core
# =============================================================================

class USBCore(LiteXModule):
    """
    USB channel multiplexer over FT601 PHY.

    Provides multiple logical channels over a single USB connection:
    - Channel 0: Etherbone (Wishbone access)
    - Channel 1+: Available for other uses (e.g., monitor stream)

    Args:
        phy: FT601Sync PHY instance
        clk_freq: System clock frequency in Hz (for timeout calculations)
    """

    def __init__(self, phy, clk_freq):
        rx_pipeline = [phy]
        tx_pipeline = [phy]

        # Depacketizer / Packetizer
        self.depacketizer = USBDepacketizer(clk_freq)
        self.packetizer   = USBPacketizer()
        rx_pipeline += [self.depacketizer]
        tx_pipeline += [self.packetizer]

        # Crossbar for channel routing
        self.crossbar = USBCrossbar()
        rx_pipeline += [self.crossbar.master]
        tx_pipeline += [self.crossbar.master]

        # Build stream pipelines
        self.rx_pipeline = stream.Pipeline(*rx_pipeline)
        self.tx_pipeline = stream.Pipeline(*reversed(tx_pipeline))
