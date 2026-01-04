#
# USB TLP Monitor - Top-Level Integration
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Complete USB monitor subsystem integrating capture engines, FIFOs,
# arbiter, and framer.
#

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream

from .layouts import DIR_RX, DIR_TX
from .capture import TLPCaptureEngine
from .fifo import MonitorHeaderFIFO, MonitorPayloadFIFO
from .arbiter import MonitorPacketArbiter

from ..core import usb_channel_description


class USBMonitorSubsystem(LiteXModule):
    """
    Complete USB TLP monitor subsystem.

    Captures TLPs from both RX (inbound) and TX (outbound) directions,
    streams them via USB channel 1.

    Parameters
    ----------
    data_width : int
        PCIe data width. Default 64.

    payload_fifo_depth : int
        Depth of payload FIFOs in 64-bit words. Default 512 (1 BRAM each).

    Interfaces
    ----------
    rx_tap_* : Signals
        RX (inbound) tap signals from depacketizer

    tx_tap_* : Signals
        TX (outbound) tap signals from packetizer

    source : stream.Endpoint
        Output to USB channel (32-bit). CDC to USB clock handled by FT601 PHY.

    Control/Status:
        rx_enable, tx_enable : Enable signals
        rx_captured, rx_dropped : RX statistics
        tx_captured, tx_dropped : TX statistics
        clear_stats : Clear all statistics
    """

    def __init__(self, data_width=64, payload_fifo_depth=512):
        # =====================================================================
        # Control Interface
        # =====================================================================

        self.rx_enable = Signal()
        self.tx_enable = Signal()
        self.clear_stats = Signal()
        self.timestamp = Signal(64)

        # =====================================================================
        # RX Tap Interface (from depacketizer)
        # =====================================================================

        # Request source
        self.rx_req_valid   = Signal()
        self.rx_req_ready   = Signal()
        self.rx_req_first   = Signal()
        self.rx_req_last    = Signal()
        self.rx_req_we      = Signal()
        self.rx_req_adr     = Signal(64)
        self.rx_req_len     = Signal(10)
        self.rx_req_req_id  = Signal(16)
        self.rx_req_tag     = Signal(8)
        self.rx_req_dat     = Signal(data_width)
        self.rx_req_first_be = Signal(4)
        self.rx_req_last_be  = Signal(4)
        self.rx_req_attr    = Signal(2)
        self.rx_req_at      = Signal(2)
        self.rx_req_bar_hit = Signal(3)

        # Completion source
        self.rx_cpl_valid   = Signal()
        self.rx_cpl_ready   = Signal()
        self.rx_cpl_first   = Signal()
        self.rx_cpl_last    = Signal()
        self.rx_cpl_adr     = Signal(64)
        self.rx_cpl_len     = Signal(10)
        self.rx_cpl_req_id  = Signal(16)
        self.rx_cpl_tag     = Signal(8)
        self.rx_cpl_dat     = Signal(data_width)
        self.rx_cpl_status  = Signal(3)
        self.rx_cpl_cmp_id  = Signal(16)
        self.rx_cpl_byte_count = Signal(12)

        # =====================================================================
        # TX Tap Interface (from packetizer)
        # =====================================================================

        # Request sink
        self.tx_req_valid   = Signal()
        self.tx_req_ready   = Signal()
        self.tx_req_first   = Signal()
        self.tx_req_last    = Signal()
        self.tx_req_we      = Signal()
        self.tx_req_adr     = Signal(64)
        self.tx_req_len     = Signal(10)
        self.tx_req_req_id  = Signal(16)
        self.tx_req_tag     = Signal(8)
        self.tx_req_dat     = Signal(data_width)
        self.tx_req_first_be = Signal(4)
        self.tx_req_last_be  = Signal(4)
        self.tx_req_attr    = Signal(2)
        self.tx_req_at      = Signal(2)
        self.tx_req_pasid_valid = Signal()
        self.tx_req_pasid   = Signal(20)
        self.tx_req_privileged = Signal()  # PMR (Privileged Mode Requested)
        self.tx_req_execute = Signal()     # ER (Execute Requested)

        # Completion sink
        self.tx_cpl_valid   = Signal()
        self.tx_cpl_ready   = Signal()
        self.tx_cpl_first   = Signal()
        self.tx_cpl_last    = Signal()
        self.tx_cpl_adr     = Signal(64)
        self.tx_cpl_len     = Signal(10)
        self.tx_cpl_req_id  = Signal(16)
        self.tx_cpl_tag     = Signal(8)
        self.tx_cpl_dat     = Signal(data_width)
        self.tx_cpl_status  = Signal(3)
        self.tx_cpl_cmp_id  = Signal(16)
        self.tx_cpl_byte_count = Signal(12)

        # =====================================================================
        # USB Output Interface
        # =====================================================================

        self.source = stream.Endpoint(usb_channel_description(32))

        # =====================================================================
        # Statistics (directly accessible for CSRs)
        # =====================================================================

        self.rx_captured = Signal(32)
        self.rx_dropped = Signal(32)
        self.rx_truncated = Signal(32)
        self.tx_captured = Signal(32)
        self.tx_dropped = Signal(32)
        self.tx_truncated = Signal(32)

        # =====================================================================
        # RX Path
        # =====================================================================

        # RX Capture Engine
        self.rx_capture = rx_capture = TLPCaptureEngine(
            data_width=data_width,
            direction=DIR_RX,
        )

        # Connect RX tap signals
        self.comb += [
            rx_capture.enable.eq(self.rx_enable),
            rx_capture.timestamp.eq(self.timestamp),
            rx_capture.clear_stats.eq(self.clear_stats),

            # Request tap
            rx_capture.tap_req_valid.eq(self.rx_req_valid),
            rx_capture.tap_req_ready.eq(self.rx_req_ready),
            rx_capture.tap_req_first.eq(self.rx_req_first),
            rx_capture.tap_req_last.eq(self.rx_req_last),
            rx_capture.tap_req_we.eq(self.rx_req_we),
            rx_capture.tap_req_adr.eq(self.rx_req_adr),
            rx_capture.tap_req_len.eq(self.rx_req_len),
            rx_capture.tap_req_req_id.eq(self.rx_req_req_id),
            rx_capture.tap_req_tag.eq(self.rx_req_tag),
            rx_capture.tap_req_dat.eq(self.rx_req_dat),
            rx_capture.tap_req_first_be.eq(self.rx_req_first_be),
            rx_capture.tap_req_last_be.eq(self.rx_req_last_be),
            rx_capture.tap_req_attr.eq(self.rx_req_attr),
            rx_capture.tap_req_at.eq(self.rx_req_at),
            rx_capture.tap_req_bar_hit.eq(self.rx_req_bar_hit),

            # Completion tap
            rx_capture.tap_cpl_valid.eq(self.rx_cpl_valid),
            rx_capture.tap_cpl_ready.eq(self.rx_cpl_ready),
            rx_capture.tap_cpl_first.eq(self.rx_cpl_first),
            rx_capture.tap_cpl_last.eq(self.rx_cpl_last),
            rx_capture.tap_cpl_adr.eq(self.rx_cpl_adr),
            rx_capture.tap_cpl_len.eq(self.rx_cpl_len),
            rx_capture.tap_cpl_req_id.eq(self.rx_cpl_req_id),
            rx_capture.tap_cpl_tag.eq(self.rx_cpl_tag),
            rx_capture.tap_cpl_dat.eq(self.rx_cpl_dat),
            rx_capture.tap_cpl_status.eq(self.rx_cpl_status),
            rx_capture.tap_cpl_cmp_id.eq(self.rx_cpl_cmp_id),
            rx_capture.tap_cpl_byte_count.eq(self.rx_cpl_byte_count),
        ]

        # RX FIFOs
        # FT601 PHY handles CDC to external USB clock.
        self.rx_header_fifo = rx_header_fifo = MonitorHeaderFIFO()
        self.rx_payload_fifo = rx_payload_fifo = MonitorPayloadFIFO(
            depth=payload_fifo_depth,
        )

        # Connect capture engine to FIFOs
        self.comb += [
            rx_capture.header_sink.connect(rx_header_fifo.sink),
            rx_capture.payload_sink.connect(rx_payload_fifo.sink),
        ]

        # Export RX stats
        self.comb += [
            self.rx_captured.eq(rx_capture.packets_captured),
            self.rx_dropped.eq(rx_capture.packets_dropped),
            self.rx_truncated.eq(rx_capture.packets_truncated),
        ]

        # =====================================================================
        # TX Path
        # =====================================================================

        # TX Capture Engine
        self.tx_capture = tx_capture = TLPCaptureEngine(
            data_width=data_width,
            direction=DIR_TX,
        )

        # Connect TX tap signals
        self.comb += [
            tx_capture.enable.eq(self.tx_enable),
            tx_capture.timestamp.eq(self.timestamp),
            tx_capture.clear_stats.eq(self.clear_stats),

            # Request tap
            tx_capture.tap_req_valid.eq(self.tx_req_valid),
            tx_capture.tap_req_ready.eq(self.tx_req_ready),
            tx_capture.tap_req_first.eq(self.tx_req_first),
            tx_capture.tap_req_last.eq(self.tx_req_last),
            tx_capture.tap_req_we.eq(self.tx_req_we),
            tx_capture.tap_req_adr.eq(self.tx_req_adr),
            tx_capture.tap_req_len.eq(self.tx_req_len),
            tx_capture.tap_req_req_id.eq(self.tx_req_req_id),
            tx_capture.tap_req_tag.eq(self.tx_req_tag),
            tx_capture.tap_req_dat.eq(self.tx_req_dat),
            tx_capture.tap_req_first_be.eq(self.tx_req_first_be),
            tx_capture.tap_req_last_be.eq(self.tx_req_last_be),
            tx_capture.tap_req_attr.eq(self.tx_req_attr),
            tx_capture.tap_req_at.eq(self.tx_req_at),
            tx_capture.tap_req_pasid_valid.eq(self.tx_req_pasid_valid),
            tx_capture.tap_req_pasid.eq(self.tx_req_pasid),
            tx_capture.tap_req_privileged.eq(self.tx_req_privileged),
            tx_capture.tap_req_execute.eq(self.tx_req_execute),

            # Completion tap
            tx_capture.tap_cpl_valid.eq(self.tx_cpl_valid),
            tx_capture.tap_cpl_ready.eq(self.tx_cpl_ready),
            tx_capture.tap_cpl_first.eq(self.tx_cpl_first),
            tx_capture.tap_cpl_last.eq(self.tx_cpl_last),
            tx_capture.tap_cpl_adr.eq(self.tx_cpl_adr),
            tx_capture.tap_cpl_len.eq(self.tx_cpl_len),
            tx_capture.tap_cpl_req_id.eq(self.tx_cpl_req_id),
            tx_capture.tap_cpl_tag.eq(self.tx_cpl_tag),
            tx_capture.tap_cpl_dat.eq(self.tx_cpl_dat),
            tx_capture.tap_cpl_status.eq(self.tx_cpl_status),
            tx_capture.tap_cpl_cmp_id.eq(self.tx_cpl_cmp_id),
            tx_capture.tap_cpl_byte_count.eq(self.tx_cpl_byte_count),
        ]

        # TX FIFOs
        # FT601 PHY handles CDC to external USB clock.
        self.tx_header_fifo = tx_header_fifo = MonitorHeaderFIFO()
        self.tx_payload_fifo = tx_payload_fifo = MonitorPayloadFIFO(
            depth=payload_fifo_depth,
        )

        # Connect capture engine to FIFOs
        self.comb += [
            tx_capture.header_sink.connect(tx_header_fifo.sink),
            tx_capture.payload_sink.connect(tx_payload_fifo.sink),
        ]

        # Export TX stats
        self.comb += [
            self.tx_captured.eq(tx_capture.packets_captured),
            self.tx_dropped.eq(tx_capture.packets_dropped),
            self.tx_truncated.eq(tx_capture.packets_truncated),
        ]

        # =====================================================================
        # Arbiter (USB clock domain)
        # =====================================================================

        # Arbiter outputs usb_channel_description format directly
        self.arbiter = arbiter = MonitorPacketArbiter()

        # Connect FIFOs to arbiter
        self.comb += [
            rx_header_fifo.source.connect(arbiter.rx_header),
            rx_payload_fifo.source.connect(arbiter.rx_payload),
            tx_header_fifo.source.connect(arbiter.tx_header),
            tx_payload_fifo.source.connect(arbiter.tx_payload),
        ]

        # Connect arbiter to output
        self.comb += arbiter.source.connect(self.source)
