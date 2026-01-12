#
# USB Etherbone Adapter - Thin wrapper around LiteEth Etherbone
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# This adapter provides USB transport for Etherbone protocol by:
# 1. Implementing USB-specific packet framing (TX/RX)
# 2. Reusing LiteEth's battle-tested protocol components
#
# Only ~100 lines of USB-specific code; everything else from LiteEth.
#

from litex.gen import *

from litex.soc.interconnect import stream
from litex.soc.interconnect.packet import Packetizer, Depacketizer, Arbiter, Dispatcher

# Import transport-agnostic components from LiteEth
from liteeth.frontend.etherbone import (
    LiteEthEtherboneProbe,
    LiteEthEtherboneRecord,
    LiteEthEtherboneWishboneMaster,
)
from liteeth.common import (
    eth_etherbone_packet_description,
    eth_etherbone_packet_user_description,
    etherbone_packet_header,
    etherbone_magic,
    etherbone_version,
)

from .core import usb_channel_description


# =============================================================================
# USB Etherbone Packet TX
# =============================================================================

class USBEtherbonePacketTX(LiteXModule):
    """
    Transmit Etherbone packets over USB channel.

    Converts eth_etherbone_packet_user_description -> usb_channel_description
    by prepending the Etherbone packet header.
    """

    def __init__(self, channel_id):
        self.sink   = sink   = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(usb_channel_description(32))

        # # #

        # Use LiteX packetizer for header insertion
        # Note: Packetizer sink needs eth_etherbone_packet_description (has header fields)
        # We receive eth_etherbone_packet_user_description and map to it
        self.packetizer = packetizer = Packetizer(
            eth_etherbone_packet_description(32),
            usb_channel_description(32),
            etherbone_packet_header
        )

        # Connect stream signals from user description to packet description
        # and fill in constant header fields
        self.comb += [
            sink.connect(packetizer.sink, keep={"valid", "last", "ready", "data"}),
            packetizer.sink.pf.eq(sink.pf),
            packetizer.sink.pr.eq(sink.pr),
            packetizer.sink.nr.eq(sink.nr),
            packetizer.sink.magic.eq(etherbone_magic),
            packetizer.sink.version.eq(etherbone_version),
            packetizer.sink.port_size.eq(32 // 8),
            packetizer.sink.addr_size.eq(32 // 8),
        ]

        # FSM to gate output until packet ready
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


# =============================================================================
# USB Etherbone Packet RX
# =============================================================================

class USBEtherbonePacketRX(LiteXModule):
    """
    Receive Etherbone packets from USB channel.

    Converts usb_channel_description -> eth_etherbone_packet_user_description
    by stripping and parsing the Etherbone packet header.
    """

    def __init__(self):
        self.sink   = sink   = stream.Endpoint(usb_channel_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        # Use LiteX depacketizer for header extraction
        # Note: Depacketizer source is eth_etherbone_packet_description (has header fields)
        # We map to eth_etherbone_packet_user_description for downstream
        self.depacketizer = depacketizer = Depacketizer(
            usb_channel_description(32),
            eth_etherbone_packet_description(32),
            etherbone_packet_header
        )
        self.comb += sink.connect(depacketizer.sink)

        # FSM for magic validation and packet routing
        self.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            If(depacketizer.source.valid,
                NextState("DROP"),
                If(depacketizer.source.magic == etherbone_magic,
                    NextState("RECEIVE")
                )
            )
        )

        # Map from packet description to user description
        # (header fields like magic/version are validated but not forwarded)
        self.comb += [
            source.last.eq(depacketizer.source.last),
            source.last_be.eq(depacketizer.source.last_be),
            source.pf.eq(depacketizer.source.pf),
            source.pr.eq(depacketizer.source.pr),
            source.nr.eq(depacketizer.source.nr),
            source.data.eq(depacketizer.source.data),
            source.length.eq(sink.length - etherbone_packet_header.length),
        ]

        fsm.act("RECEIVE",
            source.valid.eq(depacketizer.source.valid),
            depacketizer.source.ready.eq(source.ready),
            If(source.valid & source.ready & source.last,
                NextState("IDLE")
            )
        )

        fsm.act("DROP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid & depacketizer.source.last,
                NextState("IDLE")
            )
        )


# =============================================================================
# USB Etherbone (Top-Level)
# =============================================================================

class USBEtherbone(LiteXModule):
    """
    USB Etherbone: Wishbone access over USB using Etherbone protocol.

    This is a thin adapter that connects the USB crossbar to LiteEth's
    proven Etherbone implementation.

    Args:
        usb_core: USBCore instance with crossbar
        channel_id: USB channel identifier (default 0)
        buffer_depth: Depth of packet buffers (default 4)
    """

    def __init__(self, usb_core, channel_id=0, buffer_depth=4):
        # ====================================================================
        # USB-specific packet layer (our adapter code)
        # ====================================================================

        self.tx = tx = USBEtherbonePacketTX(channel_id)
        self.rx = rx = USBEtherbonePacketRX()

        # Connect to USB crossbar
        usb_port = usb_core.crossbar.get_port(channel_id)
        self.comb += [
            tx.source.connect(usb_port.sink),
            usb_port.source.connect(rx.sink),
        ]

        # ====================================================================
        # Everything below is imported from LiteEth - battle-tested code!
        # ====================================================================

        # Probe handler (responds to discovery requests)
        self.probe = probe = LiteEthEtherboneProbe()

        # Record layer (encodes/decodes Etherbone records)
        self.record = record = LiteEthEtherboneRecord(buffer_depth=buffer_depth)

        # Dispatch packets: probe requests -> probe, records -> record layer
        # pf=1 means probe request, so ~pf routes to record layer
        dispatcher = Dispatcher(rx.source, [probe.sink, record.sink])
        self.comb += dispatcher.sel.eq(~rx.source.pf)

        # Arbitrate responses: probe or record -> TX
        arbiter = Arbiter([probe.source, record.source], tx.sink)
        self.submodules += dispatcher, arbiter

        # Wishbone master for CSR/memory access
        self.master = master = LiteEthEtherboneWishboneMaster()
        self.comb += [
            record.receiver.source.connect(master.sink),
            master.source.connect(record.sender.sink),
        ]
