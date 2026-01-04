#
# USB TLP Monitor - Packet Arbiter
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Arbitrates between RX and TX monitor FIFOs and outputs
# USB channel format for the crossbar.
#

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream

from .layouts import HEADER_WORDS
from ..core import usb_channel_description


# Default USB channel for monitor
USB_MONITOR_CHANNEL = 1


class MonitorPacketArbiter(LiteXModule):
    """
    Arbitrates between RX and TX monitor FIFOs.

    Simple priority arbiter: RX takes priority over TX.
    Packet-atomic: completes entire packet before checking for next.

    Outputs usb_channel_description format for direct connection to
    USB crossbar. The crossbar's packetizer handles USB framing.

    The arbiter operates in the USB clock domain (after the async FIFOs).

    Parameters
    ----------
    channel_id : int
        USB channel ID for monitor output. Default 1.
    """

    def __init__(self, channel_id=USB_MONITOR_CHANNEL):
        # RX FIFO read interfaces (32-bit, usb_clk domain)
        self.rx_header = stream.Endpoint([("data", 32)])
        self.rx_payload = stream.Endpoint([("data", 32)])

        # TX FIFO read interfaces (32-bit, usb_clk domain)
        self.tx_header = stream.Endpoint([("data", 32)])
        self.tx_payload = stream.Endpoint([("data", 32)])

        # Output to USB crossbar (usb_channel_description format)
        self.source = stream.Endpoint(usb_channel_description(32))

        # # #

        # Current source: 0=RX, 1=TX
        from_tx = Signal()

        # Word counters
        header_count = Signal(4)
        payload_count = Signal(16)
        payload_length_dw = Signal(10)
        payload_odd = Signal()

        # Packet length in bytes (latched for USB channel description)
        packet_length_bytes = Signal(32)

        # 8 header words at 32-bit (4 x 64-bit header)
        HEADER_WORDS_32 = HEADER_WORDS * 2

        # Mux header/payload based on current source
        header_data = Signal(32)
        header_valid = Signal()
        payload_data = Signal(32)
        payload_valid = Signal()

        self.comb += [
            If(from_tx,
                header_data.eq(self.tx_header.data),
                header_valid.eq(self.tx_header.valid),
                payload_data.eq(self.tx_payload.data),
                payload_valid.eq(self.tx_payload.valid),
            ).Else(
                header_data.eq(self.rx_header.data),
                header_valid.eq(self.rx_header.valid),
                payload_data.eq(self.rx_payload.data),
                payload_valid.eq(self.rx_payload.valid),
            ),
        ]

        # USB channel description fields (constant for entire packet)
        self.comb += [
            self.source.dst.eq(channel_id),
            self.source.length.eq(packet_length_bytes),
            self.source.error.eq(0),
        ]

        # FSM
        self.fsm = fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            self.source.valid.eq(0),
            self.rx_header.ready.eq(0),
            self.tx_header.ready.eq(0),
            self.rx_payload.ready.eq(0),
            self.tx_payload.ready.eq(0),

            # Priority: RX first, then TX
            # Compute packet_length_bytes HERE so it's valid when HEADER outputs first word
            If(self.rx_header.valid,
                NextValue(from_tx, 0),
                NextValue(header_count, HEADER_WORDS_32),
                NextValue(payload_length_dw, self.rx_header.data[:10]),
                NextValue(payload_odd, self.rx_header.data[0]),
                NextValue(packet_length_bytes, (HEADER_WORDS_32 + self.rx_header.data[:10]) << 2),
                NextState("HEADER"),
            ).Elif(self.tx_header.valid,
                NextValue(from_tx, 1),
                NextValue(header_count, HEADER_WORDS_32),
                NextValue(payload_length_dw, self.tx_header.data[:10]),
                NextValue(payload_odd, self.tx_header.data[0]),
                NextValue(packet_length_bytes, (HEADER_WORDS_32 + self.tx_header.data[:10]) << 2),
                NextState("HEADER"),
            ),
        )

        # Header-only packet needs last on final header word
        header_last = Signal()
        self.comb += header_last.eq((header_count == 1) & (payload_length_dw == 0))

        fsm.act("HEADER",
            self.source.valid.eq(header_valid),
            self.source.data.eq(header_data),
            self.source.first.eq(header_count == HEADER_WORDS_32),
            self.source.last.eq(header_last),

            # Ready to appropriate header FIFO
            self.rx_header.ready.eq(~from_tx & self.source.ready),
            self.tx_header.ready.eq(from_tx & self.source.ready),
            self.rx_payload.ready.eq(0),
            self.tx_payload.ready.eq(0),

            If(header_valid & self.source.ready,
                NextValue(header_count, header_count - 1),

                If(header_count == 1,
                    NextValue(payload_count, payload_length_dw),
                    If(payload_length_dw > 0,
                        NextState("PAYLOAD"),
                    ).Else(
                        NextState("IDLE"),
                    ),
                ),
            ),
        )

        fsm.act("PAYLOAD",
            self.source.valid.eq(payload_valid),
            self.source.data.eq(payload_data),
            self.source.first.eq(0),
            self.source.last.eq(payload_count == 1),

            self.rx_header.ready.eq(0),
            self.tx_header.ready.eq(0),
            self.rx_payload.ready.eq(~from_tx & self.source.ready),
            self.tx_payload.ready.eq(from_tx & self.source.ready),

            If(payload_valid & self.source.ready,
                NextValue(payload_count, payload_count - 1),
                If(payload_count == 1,
                    If(payload_odd,
                        NextState("PAD"),
                    ).Else(
                        NextState("IDLE"),
                    ),
                ),
            ),
        )

        # Discard the extra upper DWORD emitted by the 64->32 converter on odd payload sizes.
        fsm.act("PAD",
            self.source.valid.eq(0),
            self.source.data.eq(0),
            self.source.first.eq(0),
            self.source.last.eq(0),

            self.rx_header.ready.eq(0),
            self.tx_header.ready.eq(0),
            self.rx_payload.ready.eq(~from_tx),
            self.tx_payload.ready.eq(from_tx),

            If(payload_valid,
                NextState("IDLE"),
            ),
        )
