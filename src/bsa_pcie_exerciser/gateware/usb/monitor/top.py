#
# USB TLP Monitor - Top-Level Integration
#
# Copyright (c) 2025-2026 Shareef Jalloq
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
    rx_req_source : stream.Endpoint
        RX request stream from depacketizer (tapped, not consumed).

    rx_cpl_source : stream.Endpoint
        RX completion stream from depacketizer (tapped, not consumed).

    tx_req_sink : stream.Endpoint
        TX request stream to packetizer (tapped, not consumed).

    tx_cpl_sink : stream.Endpoint
        TX completion stream to packetizer (tapped, not consumed).

    data_width : int
        PCIe data width. Default 64.

    payload_fifo_depth : int
        Depth of payload FIFOs in 64-bit words. Default 512 (1 BRAM each).

    Interfaces
    ----------
    source : stream.Endpoint
        Output to USB channel (32-bit). CDC to USB clock handled by FT601 PHY.

    Control/Status:
        rx_enable, tx_enable : Enable signals
        rx_captured, rx_dropped : RX statistics
        tx_captured, tx_dropped : TX statistics
        clear_stats : Clear all statistics
    """

    def __init__(self, rx_req_source, rx_cpl_source, tx_req_sink, tx_cpl_sink,
                 data_width=64, payload_fifo_depth=512):
        # =====================================================================
        # Control Interface
        # =====================================================================

        self.rx_enable = Signal()
        self.tx_enable = Signal()
        self.clear_stats = Signal()
        self.timestamp = Signal(64)

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
        # Pipeline Registers for Tap Signals
        # =====================================================================
        # These registers break timing paths from PCIe depacketizer/packetizer
        # to the capture engines. Since we're just tapping (observing) the
        # streams, adding a cycle of latency has no effect on functionality.

        # RX Request pipeline registers
        rx_req_valid_r   = Signal()
        rx_req_ready_r   = Signal()
        rx_req_first_r   = Signal()
        rx_req_last_r    = Signal()
        rx_req_we_r      = Signal()
        rx_req_adr_r     = Signal(64)
        rx_req_len_r     = Signal(10)
        rx_req_req_id_r  = Signal(16)
        rx_req_tag_r     = Signal(8)
        rx_req_dat_r     = Signal(data_width)
        rx_req_first_be_r = Signal(4)
        rx_req_last_be_r  = Signal(4)
        rx_req_attr_r    = Signal(2)
        rx_req_at_r      = Signal(2)
        rx_req_bar_hit_r = Signal(3)

        self.sync += [
            rx_req_valid_r.eq(rx_req_source.valid),
            rx_req_ready_r.eq(rx_req_source.ready),
            rx_req_first_r.eq(rx_req_source.first),
            rx_req_last_r.eq(rx_req_source.last),
            rx_req_we_r.eq(rx_req_source.we),
            rx_req_adr_r.eq(rx_req_source.adr),
            rx_req_len_r.eq(rx_req_source.len),
            rx_req_req_id_r.eq(rx_req_source.req_id),
            rx_req_tag_r.eq(rx_req_source.tag),
            rx_req_dat_r.eq(rx_req_source.dat),
            rx_req_first_be_r.eq(getattr(rx_req_source, 'first_be', 0)),
            rx_req_last_be_r.eq(getattr(rx_req_source, 'last_be', 0)),
            rx_req_attr_r.eq(getattr(rx_req_source, 'attr', 0)),
            rx_req_at_r.eq(getattr(rx_req_source, 'at', 0)),
            rx_req_bar_hit_r.eq(getattr(rx_req_source, 'bar_hit', 0)),
        ]

        # RX Completion pipeline registers
        rx_cpl_valid_r   = Signal()
        rx_cpl_ready_r   = Signal()
        rx_cpl_first_r   = Signal()
        rx_cpl_last_r    = Signal()
        rx_cpl_adr_r     = Signal(64)
        rx_cpl_len_r     = Signal(10)
        rx_cpl_req_id_r  = Signal(16)
        rx_cpl_tag_r     = Signal(8)
        rx_cpl_dat_r     = Signal(data_width)
        rx_cpl_status_r  = Signal(3)
        rx_cpl_cmp_id_r  = Signal(16)
        rx_cpl_byte_count_r = Signal(12)

        self.sync += [
            rx_cpl_valid_r.eq(rx_cpl_source.valid),
            rx_cpl_ready_r.eq(rx_cpl_source.ready),
            rx_cpl_first_r.eq(rx_cpl_source.first),
            rx_cpl_last_r.eq(rx_cpl_source.last),
            rx_cpl_adr_r.eq(rx_cpl_source.adr),
            rx_cpl_len_r.eq(rx_cpl_source.len),
            rx_cpl_req_id_r.eq(rx_cpl_source.req_id),
            rx_cpl_tag_r.eq(rx_cpl_source.tag),
            rx_cpl_dat_r.eq(rx_cpl_source.dat),
            rx_cpl_status_r.eq(getattr(rx_cpl_source, 'status', 0)),
            rx_cpl_cmp_id_r.eq(getattr(rx_cpl_source, 'cmp_id', 0)),
            rx_cpl_byte_count_r.eq(getattr(rx_cpl_source, 'byte_count', 0)),
        ]

        # TX Request pipeline registers
        tx_req_valid_r   = Signal()
        tx_req_ready_r   = Signal()
        tx_req_first_r   = Signal()
        tx_req_last_r    = Signal()
        tx_req_we_r      = Signal()
        tx_req_adr_r     = Signal(64)
        tx_req_len_r     = Signal(10)
        tx_req_req_id_r  = Signal(16)
        tx_req_tag_r     = Signal(8)
        tx_req_dat_r     = Signal(data_width)
        tx_req_first_be_r = Signal(4)
        tx_req_last_be_r  = Signal(4)
        tx_req_attr_r    = Signal(2)
        tx_req_at_r      = Signal(2)
        tx_req_pasid_valid_r = Signal()
        tx_req_pasid_r   = Signal(20)
        tx_req_privileged_r = Signal()
        tx_req_execute_r = Signal()

        self.sync += [
            tx_req_valid_r.eq(tx_req_sink.valid),
            tx_req_ready_r.eq(tx_req_sink.ready),
            tx_req_first_r.eq(tx_req_sink.first),
            tx_req_last_r.eq(tx_req_sink.last),
            tx_req_we_r.eq(tx_req_sink.we),
            tx_req_adr_r.eq(tx_req_sink.adr),
            tx_req_len_r.eq(tx_req_sink.len),
            tx_req_req_id_r.eq(tx_req_sink.req_id),
            tx_req_tag_r.eq(tx_req_sink.tag),
            tx_req_dat_r.eq(tx_req_sink.dat),
            tx_req_first_be_r.eq(getattr(tx_req_sink, 'first_be', 0)),
            tx_req_last_be_r.eq(getattr(tx_req_sink, 'last_be', 0)),
            tx_req_attr_r.eq(getattr(tx_req_sink, 'attr', 0)),
            tx_req_at_r.eq(getattr(tx_req_sink, 'at', 0)),
            tx_req_pasid_valid_r.eq(getattr(tx_req_sink, 'pasid_en', 0)),
            tx_req_pasid_r.eq(getattr(tx_req_sink, 'pasid_val', 0)),
            tx_req_privileged_r.eq(getattr(tx_req_sink, 'privileged', 0)),
            tx_req_execute_r.eq(getattr(tx_req_sink, 'execute', 0)),
        ]

        # TX Completion pipeline registers
        tx_cpl_valid_r   = Signal()
        tx_cpl_ready_r   = Signal()
        tx_cpl_first_r   = Signal()
        tx_cpl_last_r    = Signal()
        tx_cpl_adr_r     = Signal(64)
        tx_cpl_len_r     = Signal(10)
        tx_cpl_req_id_r  = Signal(16)
        tx_cpl_tag_r     = Signal(8)
        tx_cpl_dat_r     = Signal(data_width)
        tx_cpl_status_r  = Signal(3)
        tx_cpl_cmp_id_r  = Signal(16)
        tx_cpl_byte_count_r = Signal(12)

        self.sync += [
            tx_cpl_valid_r.eq(tx_cpl_sink.valid),
            tx_cpl_ready_r.eq(tx_cpl_sink.ready),
            tx_cpl_first_r.eq(tx_cpl_sink.first),
            tx_cpl_last_r.eq(tx_cpl_sink.last),
            tx_cpl_adr_r.eq(tx_cpl_sink.adr),
            tx_cpl_len_r.eq(tx_cpl_sink.len),
            tx_cpl_req_id_r.eq(tx_cpl_sink.req_id),
            tx_cpl_tag_r.eq(tx_cpl_sink.tag),
            tx_cpl_dat_r.eq(tx_cpl_sink.dat),
            tx_cpl_status_r.eq(getattr(tx_cpl_sink, 'status', 0)),
            tx_cpl_cmp_id_r.eq(getattr(tx_cpl_sink, 'cmp_id', 0)),
            tx_cpl_byte_count_r.eq(getattr(tx_cpl_sink, 'byte_count', 0)),
        ]

        # =====================================================================
        # RX Path
        # =====================================================================

        # RX Capture Engine
        self.rx_capture = rx_capture = TLPCaptureEngine(
            data_width=data_width,
            direction=DIR_RX,
        )

        # Connect pipelined RX tap signals to capture engine
        self.comb += [
            rx_capture.enable.eq(self.rx_enable),
            rx_capture.timestamp.eq(self.timestamp),
            rx_capture.clear_stats.eq(self.clear_stats),

            # Request tap (from pipeline registers)
            rx_capture.tap_req_valid.eq(rx_req_valid_r),
            rx_capture.tap_req_ready.eq(rx_req_ready_r),
            rx_capture.tap_req_first.eq(rx_req_first_r),
            rx_capture.tap_req_last.eq(rx_req_last_r),
            rx_capture.tap_req_we.eq(rx_req_we_r),
            rx_capture.tap_req_adr.eq(rx_req_adr_r),
            rx_capture.tap_req_len.eq(rx_req_len_r),
            rx_capture.tap_req_req_id.eq(rx_req_req_id_r),
            rx_capture.tap_req_tag.eq(rx_req_tag_r),
            rx_capture.tap_req_dat.eq(rx_req_dat_r),
            rx_capture.tap_req_first_be.eq(rx_req_first_be_r),
            rx_capture.tap_req_last_be.eq(rx_req_last_be_r),
            rx_capture.tap_req_attr.eq(rx_req_attr_r),
            rx_capture.tap_req_at.eq(rx_req_at_r),
            rx_capture.tap_req_bar_hit.eq(rx_req_bar_hit_r),

            # Completion tap (from pipeline registers)
            rx_capture.tap_cpl_valid.eq(rx_cpl_valid_r),
            rx_capture.tap_cpl_ready.eq(rx_cpl_ready_r),
            rx_capture.tap_cpl_first.eq(rx_cpl_first_r),
            rx_capture.tap_cpl_last.eq(rx_cpl_last_r),
            rx_capture.tap_cpl_adr.eq(rx_cpl_adr_r),
            rx_capture.tap_cpl_len.eq(rx_cpl_len_r),
            rx_capture.tap_cpl_req_id.eq(rx_cpl_req_id_r),
            rx_capture.tap_cpl_tag.eq(rx_cpl_tag_r),
            rx_capture.tap_cpl_dat.eq(rx_cpl_dat_r),
            rx_capture.tap_cpl_status.eq(rx_cpl_status_r),
            rx_capture.tap_cpl_cmp_id.eq(rx_cpl_cmp_id_r),
            rx_capture.tap_cpl_byte_count.eq(rx_cpl_byte_count_r),
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

        # Connect pipelined TX tap signals to capture engine
        self.comb += [
            tx_capture.enable.eq(self.tx_enable),
            tx_capture.timestamp.eq(self.timestamp),
            tx_capture.clear_stats.eq(self.clear_stats),

            # Request tap (from pipeline registers)
            tx_capture.tap_req_valid.eq(tx_req_valid_r),
            tx_capture.tap_req_ready.eq(tx_req_ready_r),
            tx_capture.tap_req_first.eq(tx_req_first_r),
            tx_capture.tap_req_last.eq(tx_req_last_r),
            tx_capture.tap_req_we.eq(tx_req_we_r),
            tx_capture.tap_req_adr.eq(tx_req_adr_r),
            tx_capture.tap_req_len.eq(tx_req_len_r),
            tx_capture.tap_req_req_id.eq(tx_req_req_id_r),
            tx_capture.tap_req_tag.eq(tx_req_tag_r),
            tx_capture.tap_req_dat.eq(tx_req_dat_r),
            tx_capture.tap_req_first_be.eq(tx_req_first_be_r),
            tx_capture.tap_req_last_be.eq(tx_req_last_be_r),
            tx_capture.tap_req_attr.eq(tx_req_attr_r),
            tx_capture.tap_req_at.eq(tx_req_at_r),
            tx_capture.tap_req_pasid_valid.eq(tx_req_pasid_valid_r),
            tx_capture.tap_req_pasid.eq(tx_req_pasid_r),
            tx_capture.tap_req_privileged.eq(tx_req_privileged_r),
            tx_capture.tap_req_execute.eq(tx_req_execute_r),

            # Completion tap (from pipeline registers)
            tx_capture.tap_cpl_valid.eq(tx_cpl_valid_r),
            tx_capture.tap_cpl_ready.eq(tx_cpl_ready_r),
            tx_capture.tap_cpl_first.eq(tx_cpl_first_r),
            tx_capture.tap_cpl_last.eq(tx_cpl_last_r),
            tx_capture.tap_cpl_adr.eq(tx_cpl_adr_r),
            tx_capture.tap_cpl_len.eq(tx_cpl_len_r),
            tx_capture.tap_cpl_req_id.eq(tx_cpl_req_id_r),
            tx_capture.tap_cpl_tag.eq(tx_cpl_tag_r),
            tx_capture.tap_cpl_dat.eq(tx_cpl_dat_r),
            tx_capture.tap_cpl_status.eq(tx_cpl_status_r),
            tx_capture.tap_cpl_cmp_id.eq(tx_cpl_cmp_id_r),
            tx_capture.tap_cpl_byte_count.eq(tx_cpl_byte_count_r),
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
