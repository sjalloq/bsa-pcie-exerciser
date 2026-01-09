#
# FT601 PHY Stub for Simulation
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Provides the same stream interface as FT601Sync but with signals
# directly accessible for cocotb testing.
#

from migen import *

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.cores.usb_fifo import phy_description


class FT601Stub(LiteXModule):
    """
    Stub FT601 PHY for USB testbench.

    Unlike real FT601Sync which interfaces to hardware pads and handles
    clock domain crossing between sys and usb, this stub:
    - Operates entirely in sys clock domain
    - Exposes stream interfaces directly for cocotb
    - Provides backpressure control

    Interface matches FT601Sync:
    - sink: stream.Endpoint(phy_description(dw)) - data TO USB host
    - source: stream.Endpoint(phy_description(dw)) - data FROM USB host

    Test Control Signals (directly driven by cocotb):
    - inject_*: Host -> Device injection path
    - capture_*: Device -> Host capture path
    - tx_backpressure: Block device->host direction

    Parameters
    ----------
    dw : int
        Data width in bits. Default 32.
    """

    def __init__(self, dw=32):
        self.dw = dw

        # =====================================================================
        # Stream Interfaces (match FT601Sync)
        # =====================================================================

        # TX: Device -> Host (data from SoC to USB host)
        self.sink = stream.Endpoint(phy_description(dw))

        # RX: Host -> Device (data from USB host to SoC)
        self.source = stream.Endpoint(phy_description(dw))

        # =====================================================================
        # Test Control Signals (directly driven by cocotb)
        # =====================================================================

        # Host -> Device injection (cocotb drives these to inject data)
        self.inject_valid = Signal()
        self.inject_ready = Signal()
        self.inject_data = Signal(dw)
        self.inject_last = Signal()

        # Device -> Host capture (cocotb reads these to capture data)
        self.capture_valid = Signal()
        self.capture_ready = Signal()
        self.capture_data = Signal(dw)
        self.capture_last = Signal()

        # Backpressure control
        self.tx_backpressure = Signal()  # Block device->host when set

        # =====================================================================
        # Host -> Device Path (inject_* -> source)
        # =====================================================================

        self.comb += [
            self.source.valid.eq(self.inject_valid),
            self.inject_ready.eq(self.source.ready),
            self.source.data.eq(self.inject_data),
            # Note: phy_description doesn't have 'last', but stream protocol
            # uses it implicitly. We keep inject_last for test control.
        ]

        # =====================================================================
        # Device -> Host Path (sink -> capture_*)
        # =====================================================================

        self.comb += [
            self.capture_valid.eq(self.sink.valid),
            self.sink.ready.eq(self.capture_ready & ~self.tx_backpressure),
            self.capture_data.eq(self.sink.data),
        ]


class FT601StubWithFIFO(LiteXModule):
    """
    FT601 Stub with internal FIFOs for easier cocotb integration.

    Adds small FIFOs on both paths to decouple the cocotb driver timing
    from the SoC timing. This makes test writing easier.

    Parameters
    ----------
    dw : int
        Data width in bits. Default 32.
    rx_depth : int
        RX FIFO depth. Default 16.
    tx_depth : int
        TX FIFO depth. Default 16.
    """

    def __init__(self, dw=32, rx_depth=16, tx_depth=16):
        self.dw = dw

        # =====================================================================
        # Stream Interfaces (match FT601Sync)
        # =====================================================================

        # RX FIFO: Host -> Device
        self.rx_fifo = rx_fifo = stream.SyncFIFO(phy_description(dw), rx_depth)
        self.source = rx_fifo.source

        # TX FIFO: Device -> Host
        self.tx_fifo = tx_fifo = stream.SyncFIFO(phy_description(dw), tx_depth)
        self.sink = tx_fifo.sink

        # =====================================================================
        # Test Control Signals (directly driven by cocotb)
        # =====================================================================

        # Host -> Device injection (cocotb drives these)
        self.inject_valid = Signal()
        self.inject_ready = Signal()
        self.inject_data = Signal(dw)

        # Device -> Host capture (cocotb reads these)
        self.capture_valid = Signal()
        self.capture_ready = Signal()
        self.capture_data = Signal(dw)

        # Backpressure control
        self.tx_backpressure = Signal()

        # FIFO status (for debugging)
        self.rx_fifo_level = Signal(max=rx_depth + 1)
        self.tx_fifo_level = Signal(max=tx_depth + 1)

        # =====================================================================
        # Host -> Device Path (inject -> rx_fifo -> source)
        # =====================================================================

        self.comb += [
            rx_fifo.sink.valid.eq(self.inject_valid),
            self.inject_ready.eq(rx_fifo.sink.ready),
            rx_fifo.sink.data.eq(self.inject_data),
        ]

        # =====================================================================
        # Device -> Host Path (sink -> tx_fifo -> capture)
        # =====================================================================

        self.comb += [
            self.capture_valid.eq(tx_fifo.source.valid),
            tx_fifo.source.ready.eq(self.capture_ready & ~self.tx_backpressure),
            self.capture_data.eq(tx_fifo.source.data),
        ]

        # Track FIFO levels
        self.comb += [
            self.rx_fifo_level.eq(rx_fifo.level),
            self.tx_fifo_level.eq(tx_fifo.level),
        ]
