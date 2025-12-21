#
# BSA PCIe Exerciser - Transaction Monitor
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Captures inbound PCIe transactions for BSA TXN_TRACE FIFO.
# Designed with hooks for future USB streaming output.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import *


class TransactionMonitor(LiteXModule):
    """
    Transaction monitor for capturing inbound PCIe requests.

    Taps into the depacketizer request stream and captures transaction
    metadata into a FIFO. The FIFO is readable via the TXN_TRACE register
    as 5 x 32-bit words per captured transaction.

    Parameters
    ----------
    data_width : int
        PCIe data width (64, 128, etc.)

    fifo_depth : int
        Number of transactions to buffer (default: 16).

    Capture Layout (per transaction):
        - Word 0: Attributes
            [0]     : we (1=write, 0=read)
            [3:1]   : bar_hit[2:0]
            [13:4]  : len[9:0]
            [17:14] : first_be[3:0]
            [21:18] : last_be[3:0]
            [23:22] : attr[1:0] (relaxed ordering, no snoop)
            [25:24] : at[1:0] (address type)
            [31:26] : reserved
        - Word 1: ADDRESS[31:0]
        - Word 2: ADDRESS[63:32]
        - Word 3: DATA[31:0]
        - Word 4: DATA[63:32]

    Interface signals (directly wired to BSARegisters):
        - enable (input): Enable transaction capture
        - clear (input): Clear FIFO (pulse)
        - fifo_data (output): 32-bit FIFO read data
        - fifo_empty (output): FIFO empty flag
        - fifo_read (input): Read next word from FIFO
    """

    def __init__(self, data_width, fifo_depth=16):
        assert data_width >= 64, "Minimum 64-bit data width"

        self.data_width = data_width

        # =====================================================================
        # Tap Interface (connects to depacketizer.req_source)
        # =====================================================================

        # Input tap - directly connected to request stream
        self.tap_valid   = Signal()
        self.tap_first   = Signal()
        self.tap_last    = Signal()
        self.tap_we      = Signal()
        self.tap_adr     = Signal(64)
        self.tap_len     = Signal(10)
        self.tap_dat     = Signal(data_width)
        self.tap_first_be = Signal(4)
        self.tap_last_be  = Signal(4)
        self.tap_bar_hit  = Signal(6)
        self.tap_attr     = Signal(2)
        self.tap_at       = Signal(2)
        self.tap_req_id   = Signal(16)
        self.tap_tag      = Signal(8)

        # =====================================================================
        # Control Interface (from BSARegisters)
        # =====================================================================

        self.enable     = Signal()      # Enable capture
        self.clear      = Signal()      # Clear FIFO (pulse)

        # =====================================================================
        # FIFO Read Interface (to BSARegisters)
        # =====================================================================

        self.fifo_data  = Signal(32)    # Current word
        self.fifo_empty = Signal()      # FIFO empty
        self.fifo_read  = Signal()      # Read strobe (advances word pointer)

        # =====================================================================
        # Internal Storage
        # =====================================================================

        # Each captured transaction is 5 x 32-bit words = 160 bits
        WORDS_PER_TXN = 5
        entry_width = 32 * WORDS_PER_TXN  # 160 bits

        # Circular buffer for transactions
        fifo_mem = Memory(entry_width, fifo_depth)
        self.specials += fifo_mem

        # Write port
        wr_port = fifo_mem.get_port(write_capable=True)
        self.specials += wr_port

        # Read port
        rd_port = fifo_mem.get_port(async_read=False)
        self.specials += rd_port

        # FIFO pointers
        wr_ptr = Signal(max=fifo_depth)
        rd_ptr = Signal(max=fifo_depth)
        count  = Signal(max=fifo_depth + 1)

        # Word counter within transaction (0-4)
        word_idx = Signal(3)

        # =====================================================================
        # Capture Logic
        # =====================================================================

        # Build capture entry from tap signals
        # Word 0: Attributes
        word0 = Signal(32)
        self.comb += word0.eq(Cat(
            self.tap_we,                    # [0]
            self.tap_bar_hit[:3],           # [3:1]
            self.tap_len,                   # [13:4]
            self.tap_first_be,              # [17:14]
            self.tap_last_be,               # [21:18]
            self.tap_attr,                  # [23:22]
            self.tap_at,                    # [25:24]
            Constant(0, 6),                 # [31:26] reserved
        ))

        # Word 1: ADDRESS[31:0]
        word1 = Signal(32)
        self.comb += word1.eq(self.tap_adr[:32])

        # Word 2: ADDRESS[63:32]
        word2 = Signal(32)
        self.comb += word2.eq(self.tap_adr[32:64])

        # Word 3: DATA[31:0]
        word3 = Signal(32)
        self.comb += word3.eq(self.tap_dat[:32])

        # Word 4: DATA[63:32]
        word4 = Signal(32)
        self.comb += word4.eq(self.tap_dat[32:64])

        # Combined entry
        capture_entry = Signal(entry_width)
        self.comb += capture_entry.eq(Cat(word0, word1, word2, word3, word4))

        # Capture on first beat of valid transaction when enabled
        do_capture = Signal()
        fifo_full = Signal()
        self.comb += [
            fifo_full.eq(count == fifo_depth),
            do_capture.eq(
                self.enable &
                self.tap_valid &
                self.tap_first &
                ~fifo_full
            ),
        ]

        # Write to FIFO
        self.comb += [
            wr_port.adr.eq(wr_ptr),
            wr_port.dat_w.eq(capture_entry),
            wr_port.we.eq(do_capture),
        ]

        # Update write pointer
        self.sync += [
            If(self.clear,
                wr_ptr.eq(0),
            ).Elif(do_capture,
                If(wr_ptr == fifo_depth - 1,
                    wr_ptr.eq(0),
                ).Else(
                    wr_ptr.eq(wr_ptr + 1),
                ),
            ),
        ]

        # =====================================================================
        # Read Logic (5 words per transaction)
        # =====================================================================

        # Current transaction from memory
        current_entry = Signal(entry_width)
        self.comb += [
            rd_port.adr.eq(rd_ptr),
            current_entry.eq(rd_port.dat_r),
        ]

        # Select word within transaction
        self.comb += [
            Case(word_idx, {
                0: self.fifo_data.eq(current_entry[0:32]),
                1: self.fifo_data.eq(current_entry[32:64]),
                2: self.fifo_data.eq(current_entry[64:96]),
                3: self.fifo_data.eq(current_entry[96:128]),
                4: self.fifo_data.eq(current_entry[128:160]),
                "default": self.fifo_data.eq(0xFFFFFFFF),
            }),
        ]

        # Empty when count == 0 and word_idx == 0
        self.comb += self.fifo_empty.eq((count == 0) & (word_idx == 0))

        # Read logic: advance word_idx, then rd_ptr when all 5 words read
        self.sync += [
            If(self.clear,
                rd_ptr.eq(0),
                word_idx.eq(0),
            ).Elif(self.fifo_read & ~self.fifo_empty,
                If(word_idx == WORDS_PER_TXN - 1,
                    # Move to next transaction
                    word_idx.eq(0),
                    If(rd_ptr == fifo_depth - 1,
                        rd_ptr.eq(0),
                    ).Else(
                        rd_ptr.eq(rd_ptr + 1),
                    ),
                ).Else(
                    word_idx.eq(word_idx + 1),
                ),
            ),
        ]

        # =====================================================================
        # FIFO Count Management
        # =====================================================================

        # Track transaction count (not word count)
        txn_consumed = Signal()
        self.comb += txn_consumed.eq(
            self.fifo_read &
            ~self.fifo_empty &
            (word_idx == WORDS_PER_TXN - 1)
        )

        self.sync += [
            If(self.clear,
                count.eq(0),
            ).Elif(do_capture & ~txn_consumed,
                count.eq(count + 1),
            ).Elif(~do_capture & txn_consumed,
                count.eq(count - 1),
            ),
            # If both happen simultaneously, count stays the same
        ]
