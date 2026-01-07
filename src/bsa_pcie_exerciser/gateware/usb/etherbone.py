#
# USB Etherbone - Wishbone over USB using Etherbone Protocol
#
# Copyright (c) 2015-2024 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Adapted from LiteEth's Etherbone implementation for USB transport.
# Original: https://github.com/enjoy-digital/liteeth/blob/master/liteeth/frontend/etherbone.py
#
# Etherbone is CERN's protocol for running Wishbone bus operations over a network.
# This implementation runs over USB instead of UDP, with these limitations:
# - No address spaces (rca/bca/wca/wff)
# - 32-bit data and address
# - 1 record per frame
#

from litex.gen import *

from litex.soc.interconnect import stream, wishbone
from litex.soc.interconnect.stream import EndpointDescription
from litex.soc.interconnect.packet import (
    Header, HeaderField, Packetizer, Depacketizer, Arbiter, Dispatcher, PacketFIFO
)

from .core import usb_channel_description


# =============================================================================
# Utility Functions
# =============================================================================

def reverse_bytes(signal):
    """Reverse byte order of a signal (for endianness conversion)."""
    n = (len(signal) + 7) // 8
    return Cat(*[signal[(n-i-1)*8:(n-i)*8] for i in range(n)])


# =============================================================================
# Etherbone Protocol Constants and Headers
# =============================================================================

etherbone_magic   = 0x4e6f
etherbone_version = 1

etherbone_packet_header_length = 8
etherbone_packet_header_fields = {
    "magic":     HeaderField(0, 0, 16),
    "version":   HeaderField(2, 4,  4),
    "nr":        HeaderField(2, 2,  1),  # No reads
    "pr":        HeaderField(2, 1,  1),  # Probe response
    "pf":        HeaderField(2, 0,  1),  # Probe flag
    "addr_size": HeaderField(3, 4,  4),
    "port_size": HeaderField(3, 0,  4),
}
etherbone_packet_header = Header(
    etherbone_packet_header_fields,
    etherbone_packet_header_length,
    swap_field_bytes=True
)

etherbone_record_header_length = 4
etherbone_record_header_fields = {
    "bca":         HeaderField(0, 0, 1),  # Bus cycle abort
    "rca":         HeaderField(0, 1, 1),  # Read cycle abort
    "rff":         HeaderField(0, 2, 1),  # Read FIFO flag
    "cyc":         HeaderField(0, 4, 1),  # Cycle flag
    "wca":         HeaderField(0, 5, 1),  # Write cycle abort
    "wff":         HeaderField(0, 6, 1),  # Write FIFO flag
    "byte_enable": HeaderField(1, 0, 8),
    "wcount":      HeaderField(2, 0, 8),  # Write count
    "rcount":      HeaderField(3, 0, 8),  # Read count
}
etherbone_record_header = Header(
    etherbone_record_header_fields,
    etherbone_record_header_length,
    swap_field_bytes=True
)


# =============================================================================
# Stream Descriptions
# =============================================================================

def _remove_from_layout(layout, *args):
    """Remove specified fields from a layout."""
    return [f for f in layout if f[0] not in args]


def etherbone_packet_description(dw):
    """Raw Etherbone packet with full header."""
    param_layout = etherbone_packet_header.get_layout()
    payload_layout = [
        ("data",  dw),
        ("error", dw//8),
    ]
    return EndpointDescription(payload_layout, param_layout)


def etherbone_packet_user_description(dw):
    """Etherbone packet for user with stripped header fields."""
    param_layout = etherbone_packet_header.get_layout()
    param_layout = _remove_from_layout(param_layout,
        "magic", "port_size", "addr_size", "version")
    param_layout += usb_channel_description(dw).param_layout
    payload_layout = [
        ("data",  dw),
        ("error", dw//8),
    ]
    return EndpointDescription(payload_layout, param_layout)


def etherbone_record_description(dw):
    """Etherbone record with header fields."""
    param_layout = etherbone_record_header.get_layout()
    payload_layout = [
        ("data",  dw),
        ("error", dw//8),
    ]
    return EndpointDescription(payload_layout, param_layout)


def etherbone_mmap_description(dw):
    """Memory-mapped access description (decoded from records)."""
    param_layout = [
        ("we",        1),
        ("count",     8),
        ("base_addr", 32),
        ("be",        dw//8),
    ]
    payload_layout = [
        ("addr", 32),
        ("data", dw),
    ]
    return EndpointDescription(payload_layout, param_layout)


# =============================================================================
# Etherbone Packet Layer
# =============================================================================

class EtherbonePacketPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            etherbone_packet_description(32),
            usb_channel_description(32),
            etherbone_packet_header
        )


class EtherbonePacketTX(LiteXModule):
    """Transmit Etherbone packets over USB channel."""

    def __init__(self, channel_id):
        self.sink   = sink   = stream.Endpoint(etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(usb_channel_description(32))

        # # #

        self.packetizer = packetizer = EtherbonePacketPacketizer()
        self.comb += [
            sink.connect(packetizer.sink, keep={"valid", "last", "ready", "data"}),
            sink.connect(packetizer.sink, keep={"pf", "pr", "nr"}),
            packetizer.sink.magic.eq(etherbone_magic),
            packetizer.sink.port_size.eq(32//8),
            packetizer.sink.addr_size.eq(32//8),
            packetizer.sink.version.eq(etherbone_version),
        ]

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(packetizer.source.valid,
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.source.connect(source),
            source.dst.eq(channel_id),
            source.length.eq(sink.length + etherbone_packet_header.length),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )


class EtherbonePacketDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            usb_channel_description(32),
            etherbone_packet_description(32),
            etherbone_packet_header
        )


class EtherbonePacketRX(LiteXModule):
    """Receive Etherbone packets from USB channel."""

    def __init__(self):
        self.sink   = sink   = stream.Endpoint(usb_channel_description(32))
        self.source = source = stream.Endpoint(etherbone_packet_user_description(32))

        # # #

        self.depacketizer = depacketizer = EtherbonePacketDepacketizer()
        self.comb += sink.connect(depacketizer.sink)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(depacketizer.source.valid,
                NextState("DROP"),
                If(depacketizer.source.magic == etherbone_magic,
                    NextState("RECEIVE")
                )
            )
        )

        self.comb += [
            depacketizer.source.connect(source, keep={"last", "pf", "pr", "nr", "data"}),
            source.length.eq(sink.length - etherbone_packet_header.length),
        ]

        fsm.act("RECEIVE",
            depacketizer.source.connect(source, keep={"valid", "ready"}),
            If(source.valid & source.ready & source.last,
                NextState("IDLE")
            )
        )

        fsm.act("DROP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid &
               depacketizer.source.last &
               depacketizer.source.ready,
                NextState("IDLE")
            )
        )


class EtherbonePacket(LiteXModule):
    """Etherbone packet layer - connects to USB crossbar."""

    def __init__(self, usb_core, channel_id):
        self.tx = tx = EtherbonePacketTX(channel_id)
        self.rx = rx = EtherbonePacketRX()

        usb_port = usb_core.crossbar.get_port(channel_id)
        self.comb += [
            tx.source.connect(usb_port.sink),
            usb_port.source.connect(rx.sink),
        ]

        self.sink, self.source = self.tx.sink, self.rx.source


# =============================================================================
# Etherbone Probe
# =============================================================================

class EtherboneProbe(LiteXModule):
    """Respond to Etherbone probe/discovery requests."""

    def __init__(self):
        self.sink   = sink   = stream.Endpoint(etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(etherbone_packet_user_description(32))

        # # #

        # Buffer probe requests (needed for proper handshaking)
        self.fifo = fifo = PacketFIFO(
            etherbone_packet_user_description(32),
            payload_depth = 1,
            param_depth   = 1,
            buffered      = False,
        )
        self.comb += sink.connect(fifo.sink)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(fifo.source.valid,
                NextState("PROBE_RESPONSE")
            )
        )
        fsm.act("PROBE_RESPONSE",
            fifo.source.connect(source),
            source.pf.eq(0),  # Clear probe flag
            source.pr.eq(1),  # Set probe response
            If(source.valid & source.ready & source.last,
                NextState("IDLE")
            )
        )


# =============================================================================
# Etherbone Record Layer
# =============================================================================

class EtherboneRecordPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            etherbone_record_description(32),
            etherbone_packet_user_description(32),
            etherbone_record_header
        )


class EtherboneRecordDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            etherbone_packet_user_description(32),
            etherbone_record_description(32),
            etherbone_record_header
        )


class EtherboneRecordReceiver(LiteXModule):
    """Decode Etherbone records into memory-mapped operations."""

    def __init__(self, buffer_depth=4):
        self.sink   = sink   = stream.Endpoint(etherbone_record_description(32))
        self.source = source = stream.Endpoint(etherbone_mmap_description(32))

        # # #

        assert buffer_depth <= 256

        self.fifo = fifo = PacketFIFO(
            etherbone_record_description(32),
            payload_depth = buffer_depth,
            param_depth   = 1,
            buffered      = True,
        )
        self.comb += sink.connect(fifo.sink)

        base_addr = Signal(32, reset_less=True)
        base_addr_update = Signal()
        self.sync += If(base_addr_update, base_addr.eq(fifo.source.data))

        count = Signal(max=512, reset_less=True)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            fifo.source.ready.eq(1),
            NextValue(count, 0),
            If(fifo.source.valid,
                base_addr_update.eq(1),
                If(fifo.source.wcount,
                    NextState("RECEIVE_WRITES")
                ).Elif(fifo.source.rcount,
                    NextState("RECEIVE_READS")
                )
            )
        )

        fsm.act("RECEIVE_WRITES",
            source.valid.eq(fifo.source.valid),
            source.last.eq(count == fifo.source.wcount - 1),
            source.count.eq(fifo.source.wcount),
            source.be.eq(fifo.source.byte_enable),
            source.addr.eq(base_addr[2:] + count),
            source.we.eq(1),
            source.data.eq(fifo.source.data),
            fifo.source.ready.eq(source.ready),
            If(source.valid & source.ready,
                NextValue(count, count + 1),
                If(source.last,
                    If(fifo.source.rcount,
                        NextState("RECEIVE_BASE_RET_ADDR")
                    ).Else(
                        NextState("IDLE")
                    )
                )
            )
        )

        fsm.act("RECEIVE_BASE_RET_ADDR",
            NextValue(count, 0),
            If(fifo.source.valid,
                base_addr_update.eq(1),
                NextState("RECEIVE_READS")
            )
        )

        fsm.act("RECEIVE_READS",
            source.valid.eq(fifo.source.valid),
            source.last.eq(count == fifo.source.rcount - 1),
            source.count.eq(fifo.source.rcount),
            source.be.eq(fifo.source.byte_enable),
            source.base_addr.eq(base_addr),
            source.addr.eq(fifo.source.data[2:]),
            fifo.source.ready.eq(source.ready),
            If(source.valid & source.ready,
                NextValue(count, count + 1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )


class EtherboneRecordSender(LiteXModule):
    """Encode memory-mapped responses into Etherbone records."""

    def __init__(self, buffer_depth=4):
        self.sink   = sink   = stream.Endpoint(etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(etherbone_record_description(32))

        # # #

        assert buffer_depth <= 256

        self.fifo = fifo = PacketFIFO(
            etherbone_mmap_description(32),
            payload_depth = buffer_depth,
            param_depth   = 1,
            buffered      = True,
        )
        self.comb += sink.connect(fifo.sink)

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(fifo.source.valid,
                NextState("SEND_BASE_ADDRESS")
            )
        )

        self.comb += [
            source.byte_enable.eq(fifo.source.be),
            If(fifo.source.we,
                source.wcount.eq(fifo.source.count),
            ).Else(
                source.rcount.eq(fifo.source.count),
            )
        ]

        fsm.act("SEND_BASE_ADDRESS",
            source.valid.eq(1),
            source.last.eq(0),
            source.data.eq(fifo.source.base_addr),
            If(source.ready,
                NextState("SEND_DATA")
            )
        )

        fsm.act("SEND_DATA",
            source.valid.eq(1),
            source.last.eq(fifo.source.last),
            source.data.eq(fifo.source.data),
            If(source.valid & source.ready,
                fifo.source.ready.eq(1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )


class EtherboneRecord(LiteXModule):
    """
    Etherbone record layer.

    Limitation: For simplicity we only support 1 record per packet.
    """

    def __init__(self, endianness="big", buffer_depth=4):
        self.sink   = sink   = stream.Endpoint(etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(etherbone_packet_user_description(32))

        # # #

        # Receive record, decode it and generate mmap stream.
        self.depacketizer = depacketizer = EtherboneRecordDepacketizer()
        self.receiver     = receiver     = EtherboneRecordReceiver(buffer_depth)
        self.comb += [
            sink.connect(depacketizer.sink),
            depacketizer.source.connect(receiver.sink),
        ]
        if endianness == "big":
            self.comb += receiver.sink.data.eq(reverse_bytes(depacketizer.source.data))

        # Receive mmap stream, encode it and send records.
        self.sender     = sender     = EtherboneRecordSender(buffer_depth)
        self.packetizer = packetizer = EtherboneRecordPacketizer()
        self.record_buffer = record_buffer = stream.Buffer(
            etherbone_record_description(32)
        )
        self.comb += [
            sender.source.connect(record_buffer.sink),
            record_buffer.source.connect(packetizer.sink),
            packetizer.source.connect(source),
            source.length.eq(
                etherbone_record_header.length +
                (sender.source.wcount != 0) * 4 + sender.source.wcount * 4 +
                (sender.source.rcount != 0) * 4 + sender.source.rcount * 4
            ),
        ]
        if endianness == "big":
            self.comb += packetizer.sink.data.eq(reverse_bytes(sender.source.data))


# =============================================================================
# Etherbone Wishbone Master
# =============================================================================

class EtherboneWishboneMaster(LiteXModule):
    """Convert Etherbone memory-mapped operations to Wishbone transactions."""

    def __init__(self):
        self.sink   = sink   = stream.Endpoint(etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(etherbone_mmap_description(32))
        self.bus    = bus    = wishbone.Interface()

        # # #

        data_update = Signal()

        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid,
                sink.ready.eq(0),
                If(sink.we,
                    NextState("WRITE_DATA")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )

        fsm.act("WRITE_DATA",
            bus.adr.eq(sink.addr),
            bus.dat_w.eq(sink.data),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.valid),
            bus.we.eq(1),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                sink.ready.eq(1),
                If(sink.last,
                    NextState("IDLE")
                )
            )
        )

        fsm.act("READ_DATA",
            bus.adr.eq(sink.addr),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.valid),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                data_update.eq(1),
                NextState("SEND_DATA")
            )
        )

        # Register read data and pass through other signals
        self.sync += [
            sink.connect(source, keep={"base_addr", "addr", "count", "be"}),
            source.we.eq(1),
            If(data_update, source.data.eq(bus.dat_r)),
        ]

        fsm.act("SEND_DATA",
            sink.connect(source, keep={"valid", "last", "ready"}),
            If(source.valid & source.ready,
                If(source.last,
                    NextState("IDLE")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )


# =============================================================================
# Etherbone (Top-Level)
# =============================================================================

class Etherbone(LiteXModule):
    """
    USB Etherbone: Wishbone access over USB using Etherbone protocol.

    Connects to a USBCore's crossbar on the specified channel_id.

    Args:
        usb_core: USBCore instance with crossbar
        channel_id: USB channel identifier (default 0)
        buffer_depth: Depth of packet buffers (default 4)
    """

    def __init__(self, usb_core, channel_id=0, buffer_depth=4):
        # Encode/decode Etherbone packets
        self.packet = packet = EtherbonePacket(usb_core, channel_id)

        # Packets can be probe (discovery) or records (read/write operations)
        self.probe  = probe  = EtherboneProbe()
        self.record = record = EtherboneRecord(buffer_depth=buffer_depth)

        # Arbitrate/dispatch probe vs record packets
        dispatcher = Dispatcher(packet.source, [probe.sink, record.sink])
        self.comb += dispatcher.sel.eq(~packet.source.pf)
        arbiter = Arbiter([probe.source, record.source], packet.sink)
        self.submodules += dispatcher, arbiter

        # Wishbone master for memory-mapped access
        self.master = master = EtherboneWishboneMaster()
        self.comb += [
            record.receiver.source.connect(master.sink),
            master.source.connect(record.sender.sink),
        ]
