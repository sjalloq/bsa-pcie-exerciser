#
# FT601 USB 3.0 Synchronous FIFO PHY
#
# Copyright (c) 2016 Florent Kermarrec <florent@enjoy-digital.fr>
# Copyright (c) 2018-2019 Pierre-Olivier Vauboin <po@lambdaconcept.com>
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Ported from enjoy-digital/pcie_screamer with adaptations for BSA Exerciser.
#

from migen import *
from migen.fhdl.specials import Tristate

from litex.gen import *
from litex.soc.interconnect import stream
from litex.soc.cores.usb_fifo import phy_description


class FT601Sync(LiteXModule):
    """
    FT601 USB 3.0 Synchronous FIFO PHY.

    Provides stream interfaces for USB communication:
    - sink: Data to send to USB host (sys clock domain)
    - source: Data received from USB host (sys clock domain)

    Internally handles clock domain crossing between sys and usb (FT601's 100MHz).

    The FSM arbitrates between read and write operations with a timeout
    to ensure fair access when both directions have data pending.

    Pads interface (from platform.request("usb_fifo")):
    - clk: 100MHz clock from FT601
    - data[31:0]: Bidirectional data bus
    - be[3:0]: Byte enables
    - rxf_n: RX FIFO not empty (active low)
    - txe_n: TX FIFO not full (active low)
    - rd_n: Read strobe (active low)
    - wr_n: Write strobe (active low)
    - oe_n: Output enable (active low)
    - siwu_n: Send immediate / wake up (active low)
    - rst_n: Reset (active low)
    """

    def __init__(self, pads, dw=32, timeout=1024):
        # Clock domain crossing FIFOs
        # Read: USB -> sys
        read_fifo = ClockDomainsRenamer({"write": "usb", "read": "sys"})(
            stream.AsyncFIFO(phy_description(dw), 128)
        )
        # Write: sys -> USB
        write_fifo = ClockDomainsRenamer({"write": "sys", "read": "usb"})(
            stream.AsyncFIFO(phy_description(dw), 128)
        )

        # Small buffer in USB domain for read timing
        read_buffer = ClockDomainsRenamer("usb")(
            stream.SyncFIFO(phy_description(dw), 4)
        )
        self.comb += read_buffer.source.connect(read_fifo.sink)

        self.read_fifo = read_fifo
        self.read_buffer = read_buffer
        self.write_fifo = write_fifo

        # Stream interfaces (sys clock domain)
        self.sink = write_fifo.sink      # TX: sys -> USB
        self.source = read_fifo.source   # RX: USB -> sys

        # ---------------------------------------------------------------------
        # Tristate data bus
        # ---------------------------------------------------------------------
        tdata_w = Signal(dw)
        data_r = Signal(dw)
        data_oe = Signal()
        self.specials += Tristate(pads.data, tdata_w, data_oe, data_r)

        # Register data for ODDR output
        data_w = Signal(dw)
        _data_w = Signal(dw)
        self.sync.usb += _data_w.eq(data_w)

        # ODDR for data output (improves timing)
        for i in range(dw):
            self.specials += Instance("ODDR",
                p_DDR_CLK_EDGE = "SAME_EDGE",
                i_C  = ClockSignal("usb"),
                i_CE = 1,
                i_S  = 0,
                i_R  = 0,
                i_D1 = _data_w[i],
                i_D2 = data_w[i],
                o_Q  = tdata_w[i],
            )

        # ---------------------------------------------------------------------
        # Control signals with ODDR
        # ---------------------------------------------------------------------
        rd_n = Signal()
        _rd_n = Signal(reset=1)
        wr_n = Signal()
        _wr_n = Signal(reset=1)
        oe_n = Signal()
        _oe_n = Signal(reset=1)

        self.sync.usb += [
            _rd_n.eq(rd_n),
            _wr_n.eq(wr_n),
            _oe_n.eq(oe_n),
        ]

        self.specials += [
            Instance("ODDR",
                p_DDR_CLK_EDGE = "SAME_EDGE",
                i_C  = ClockSignal("usb"),
                i_CE = 1,
                i_S  = 0,
                i_R  = 0,
                i_D1 = _rd_n,
                i_D2 = rd_n,
                o_Q  = pads.rd_n,
            ),
            Instance("ODDR",
                p_DDR_CLK_EDGE = "SAME_EDGE",
                i_C  = ClockSignal("usb"),
                i_CE = 1,
                i_S  = 0,
                i_R  = 0,
                i_D1 = _wr_n,
                i_D2 = wr_n,
                o_Q  = pads.wr_n,
            ),
            Instance("ODDR",
                p_DDR_CLK_EDGE = "SAME_EDGE",
                i_C  = ClockSignal("usb"),
                i_CE = 1,
                i_S  = 0,
                i_R  = 0,
                i_D1 = _oe_n,
                i_D2 = oe_n,
                o_Q  = pads.oe_n,
            ),
        ]

        # ---------------------------------------------------------------------
        # Static signals
        # ---------------------------------------------------------------------
        self.comb += [
            pads.rst_n.eq(~ResetSignal("usb")),  # active low reset
            pads.be.eq(0xF),                      # All bytes enabled
            pads.siwu_n.eq(1),                    # No send immediate
            data_oe.eq(oe_n),                     # OE active when oe_n is high (we're writing)
        ]

        # ---------------------------------------------------------------------
        # FSM State
        # ---------------------------------------------------------------------
        tempsendval = Signal(dw)
        temptosend = Signal()
        tempreadval = Signal(dw)
        temptoread = Signal()

        wants_read = Signal()
        wants_write = Signal()
        cnt_write = Signal(max=timeout + 1)
        cnt_read = Signal(max=timeout + 1)
        first_write = Signal()

        self.comb += [
            wants_read.eq(~temptoread & ~pads.rxf_n),
            wants_write.eq((temptosend | write_fifo.source.valid) & (pads.txe_n == 0)),
        ]

        # ---------------------------------------------------------------------
        # FSM (USB clock domain)
        # ---------------------------------------------------------------------
        self.fsm = fsm = ClockDomainsRenamer("usb")(FSM(reset_state="IDLE"))

        # Handle pending read data outside FSM
        self.sync.usb += [
            If(~fsm.ongoing("READ"),
                If(temptoread,
                    If(read_buffer.sink.ready,
                        temptoread.eq(0),
                    )
                )
            )
        ]

        self.comb += [
            If(~fsm.ongoing("READ"),
                If(temptoread,
                    read_buffer.sink.data.eq(tempreadval),
                    read_buffer.sink.valid.eq(1),
                )
            )
        ]

        fsm.act("IDLE",
            rd_n.eq(1),
            wr_n.eq(1),
            If(wants_write,
                oe_n.eq(1),
                NextValue(cnt_write, 0),
                NextValue(first_write, 1),
                NextState("WRITE"),
            ).Elif(wants_read,
                oe_n.eq(0),
                NextState("RDWAIT"),
            ).Else(
                oe_n.eq(1),
            )
        )

        fsm.act("WRITE",
            If(wants_read,
                NextValue(cnt_write, cnt_write + 1),
            ),
            NextValue(first_write, 0),
            rd_n.eq(1),

            If(pads.txe_n,
                # TX FIFO full - go back to IDLE
                oe_n.eq(1),
                wr_n.eq(1),
                write_fifo.source.ready.eq(0),
                If(write_fifo.source.valid & ~first_write,
                    NextValue(temptosend, 1),
                ),
                NextState("IDLE"),
            ).Elif(temptosend,
                # Send previously buffered data
                oe_n.eq(1),
                data_w.eq(tempsendval),
                wr_n.eq(0),
                NextValue(temptosend, 0),
            ).Elif(cnt_write > timeout,
                # Timeout - switch to read
                oe_n.eq(0),
                NextState("RDWAIT"),
            ).Elif(write_fifo.source.valid,
                # Send data from FIFO
                oe_n.eq(1),
                data_w.eq(write_fifo.source.data),
                write_fifo.source.ready.eq(1),
                NextValue(tempsendval, write_fifo.source.data),
                NextValue(temptosend, 0),
                wr_n.eq(0),
            ).Else(
                # No more data - go back to IDLE
                oe_n.eq(1),
                wr_n.eq(1),
                NextValue(temptosend, 0),
                NextState("IDLE"),
            )
        )

        fsm.act("RDWAIT",
            # One cycle wait for OE to propagate
            rd_n.eq(0),
            oe_n.eq(0),
            wr_n.eq(1),
            NextValue(cnt_read, 0),
            NextState("READ"),
        )

        fsm.act("READ",
            If(wants_write,
                NextValue(cnt_read, cnt_read + 1),
            ),
            wr_n.eq(1),

            If(pads.rxf_n,
                # RX FIFO empty - go back to IDLE
                oe_n.eq(0),
                rd_n.eq(1),
                NextState("IDLE"),
            ).Elif(cnt_read > timeout,
                # Timeout - switch to write
                NextValue(cnt_write, 0),
                NextValue(first_write, 1),
                NextState("WRITE"),
                oe_n.eq(1),
            ).Else(
                # Read data
                oe_n.eq(0),
                read_buffer.sink.valid.eq(1),
                read_buffer.sink.data.eq(data_r),
                NextValue(tempreadval, data_r),
                If(read_buffer.sink.ready,
                    rd_n.eq(0),
                ).Else(
                    NextValue(temptoread, 1),
                    NextState("IDLE"),
                    rd_n.eq(1),
                )
            )
        )

        # Debug signals
        self.rd_n = rd_n
        self.wr_n = wr_n
        self.oe_n = oe_n
        self.data_w = data_w
        self.data_r = data_r
