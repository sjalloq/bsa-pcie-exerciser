#
# BSA PCIe Exerciser - Transaction Monitor
#
# Copyright (c) 2025-2026 Shareef Jalloq
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
        - Word 0: TX_ATTRIBUTES (ACS encoding)
            [0]     : type (CFG only: 0=Type0, 1=Type1)
            [1]     : R/W (1=read, 0=write)
            [2]     : CFG/MEM (1=CFG, 0=MEM)
            [15:3]  : reserved
            [31:16] : byte size one-hot (bit = log2(bytes))
        - Word 1: ADDRESS[31:0]
        - Word 2: ADDRESS[63:32]
        - Word 3: DATA[31:0]
        - Word 4: DATA[63:32]

    Interface signals (directly wired to BSARegisters):
        - enable (input): Enable transaction capture
        - clear (input): Clear FIFO and overflow (pulse)
        - overflow (output): Sticky flag, set when transaction dropped due to full FIFO
        - count (output): Number of transactions currently in FIFO
        - fifo_data (output): 32-bit FIFO read data
        - fifo_empty (output): FIFO empty flag
        - fifo_read (input): Read next word from FIFO

    Overflow Behavior:
        When the FIFO is full and a new transaction arrives, the overflow flag
        is set and the transaction is dropped. Once overflow is set, no further
        transactions are captured until software writes the clear bit. This
        ensures software is aware that transactions were lost.
    """

    def __init__(self, data_width, fifo_depth=16):
        assert data_width >= 64, "Minimum 64-bit data width"

        self.data_width = data_width
        self.fifo_depth = fifo_depth

        # =====================================================================
        # Tap Interface (connects to depacketizer.req_source)
        # =====================================================================

        # Input tap - request stream (memory transactions)
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

        # Input tap - configuration transactions
        self.tap_cfg_valid = Signal()
        self.tap_cfg_first = Signal()
        self.tap_cfg_last  = Signal()
        self.tap_cfg_we    = Signal()
        self.tap_cfg_type  = Signal()     # 0=Type0, 1=Type1
        self.tap_cfg_bus_number  = Signal(8)
        self.tap_cfg_device_no   = Signal(5)
        self.tap_cfg_func        = Signal(3)
        self.tap_cfg_ext_reg     = Signal(4)
        self.tap_cfg_register_no = Signal(6)
        self.tap_cfg_tag         = Signal(8)
        self.tap_cfg_first_be    = Signal(4)
        self.tap_cfg_dat         = Signal(data_width)

        # =====================================================================
        # Control Interface (from BSARegisters)
        # =====================================================================

        self.enable     = Signal()      # Enable capture
        self.clear      = Signal()      # Clear FIFO and overflow (pulse)

        # =====================================================================
        # Status Interface (to BSARegisters)
        # =====================================================================

        self.overflow   = Signal()      # Sticky overflow flag (RO)
        self.count      = Signal(8)     # Transaction count in FIFO (RO)

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

        # Build capture entry from tap signals (ACS TXN_TRACE encoding)
        assert data_width == 64, "Transaction monitor TXN_TRACE encoding assumes 64-bit data width"

        bytes_per_beat = data_width // 8

        # Track current beat address for memory requests
        mem_base_addr = Signal(64)
        mem_beat_index = Signal(16)
        mem_addr = Signal(64)
        mem_base_addr_cap = Signal(64)
        mem_beat_index_cap = Signal(16)
        self.comb += [
            mem_base_addr_cap.eq(Mux(self.tap_first, self.tap_adr, mem_base_addr)),
            mem_beat_index_cap.eq(Mux(self.tap_first, 0, mem_beat_index)),
            mem_addr.eq(mem_base_addr_cap + mem_beat_index_cap * bytes_per_beat),
        ]

        self.sync += [
            If(self.clear,
                mem_base_addr.eq(0),
                mem_beat_index.eq(0),
            ).Elif(self.tap_valid & self.tap_first,
                mem_base_addr.eq(self.tap_adr),
                mem_beat_index.eq(0),
            ).Elif(self.tap_valid,
                mem_beat_index.eq(mem_beat_index + 1),
            ),
        ]

        # Configuration address encoding (BDF + register offset)
        cfg_addr = Signal(64)
        self.comb += cfg_addr.eq(
            (self.tap_cfg_bus_number << 20) |
            (self.tap_cfg_device_no  << 15) |
            (self.tap_cfg_func       << 12) |
            (self.tap_cfg_ext_reg    << 8)  |
            (self.tap_cfg_register_no << 2)
        )

        # Select capture source: configuration has priority if both present
        use_cfg = Signal()
        use_mem = Signal()
        cap_valid = Signal()
        self.comb += [
            use_cfg.eq(self.tap_cfg_valid),
            use_mem.eq(self.tap_valid & ~self.tap_cfg_valid),
            cap_valid.eq(use_cfg | use_mem),
        ]

        cap_we = Signal()
        cap_addr = Signal(64)
        cap_dat = Signal(data_width)
        cap_first_be = Signal(4)
        cap_last_be = Signal(4)
        cap_cfg_type = Signal()

        self.comb += [
            cap_we.eq(Mux(use_cfg, self.tap_cfg_we, self.tap_we)),
            cap_addr.eq(Mux(use_cfg, cfg_addr, mem_addr)),
            cap_dat.eq(Mux(use_cfg, self.tap_cfg_dat, self.tap_dat)),
            cap_first_be.eq(Mux(use_cfg, self.tap_cfg_first_be, self.tap_first_be)),
            cap_last_be.eq(Mux(use_cfg, 0, self.tap_last_be)),
            cap_cfg_type.eq(Mux(use_cfg, self.tap_cfg_type, 0)),
        ]

        # Byte enables for this beat (64-bit data width)
        mem_byte_en = Signal(8)
        cfg_byte_en = Signal(8)
        byte_en = Signal(8)

        self.comb += [
            mem_byte_en.eq(0),
            If(self.tap_first & self.tap_last,
                If(self.tap_len == 1,
                    mem_byte_en.eq(Cat(self.tap_first_be, Constant(0, 4))),
                ).Else(
                    mem_byte_en.eq(Cat(self.tap_first_be, self.tap_last_be)),
                ),
            ).Elif(self.tap_first,
                mem_byte_en.eq(Cat(self.tap_first_be, Constant(0xF, 4))),
            ).Elif(self.tap_last,
                If(self.tap_len[0],
                    mem_byte_en.eq(Cat(self.tap_last_be, Constant(0, 4))),
                ).Else(
                    mem_byte_en.eq(Cat(Constant(0xF, 4), self.tap_last_be)),
                ),
            ).Else(
                mem_byte_en.eq(0xFF),
            ),
            cfg_byte_en.eq(Cat(self.tap_cfg_first_be, Constant(0, 4))),
            byte_en.eq(Mux(use_cfg, cfg_byte_en, mem_byte_en)),
        ]

        # Byte size encoding (upper 16 bits): one-hot log2(bytes)
        bytes_in_beat = Signal(4)
        self.comb += bytes_in_beat.eq(
            byte_en[0] + byte_en[1] + byte_en[2] + byte_en[3] +
            byte_en[4] + byte_en[5] + byte_en[6] + byte_en[7]
        )

        size_onehot = Signal(16)
        self.comb += size_onehot.eq(
            Mux(bytes_in_beat == 1, 1 << 0,
            Mux(bytes_in_beat == 2, 1 << 1,
            Mux(bytes_in_beat == 4, 1 << 2,
            Mux(bytes_in_beat == 8, 1 << 3, 0))))
        )

        # Lower 16 bits: [2]=cfg/mem, [1]=read, [0]=type (Type0/Type1)
        read_bit = Signal()
        tx_attr_lower = Signal(16)
        self.comb += [
            read_bit.eq(~cap_we),
            tx_attr_lower.eq(Cat(
                cap_cfg_type,     # [0] type (cfg only)
                read_bit,         # [1] read=1, write=0
                use_cfg,          # [2] cfg=1, mem=0
                Constant(0, 13),  # [15:3] reserved
            )),
        ]

        # Word 0: TX_ATTRIBUTES
        word0 = Signal(32)
        self.comb += word0.eq(Cat(tx_attr_lower, size_onehot))

        # Word 1: ADDRESS[31:0]
        word1 = Signal(32)
        self.comb += word1.eq(cap_addr[:32])

        # Word 2: ADDRESS[63:32]
        word2 = Signal(32)
        self.comb += word2.eq(cap_addr[32:64])

        # Word 3: DATA[31:0]
        word3 = Signal(32)
        self.comb += word3.eq(cap_dat[:32])

        # Word 4: DATA[63:32]
        word4 = Signal(32)
        self.comb += word4.eq(cap_dat[32:64])

        # Combined entry
        capture_entry = Signal(entry_width)
        self.comb += capture_entry.eq(Cat(word0, word1, word2, word3, word4))

        # Overflow detection and lockout
        # Once overflow is set, no further captures until clear
        fifo_full = Signal()
        overflow_reg = Signal()
        txn_arriving = Signal()
        txn_dropped = Signal()

        # Exclude TXN_CTRL register (BAR0 offset 0x44) from capture.
        # This prevents enable/disable writes from polluting the trace,
        # which is required for BSA ACS test compatibility.
        REG_TXN_CTRL = 0x044
        is_txn_ctrl = Signal()
        self.comb += is_txn_ctrl.eq(
            self.tap_valid & self.tap_bar_hit[0] & (self.tap_adr[:12] == REG_TXN_CTRL)
        )

        self.comb += [
            fifo_full.eq(count == fifo_depth),
            txn_arriving.eq(self.enable & cap_valid & ~is_txn_ctrl),
            txn_dropped.eq(txn_arriving & (fifo_full | overflow_reg)),
            self.overflow.eq(overflow_reg),
        ]

        # Capture on each beat when enabled and not in overflow
        do_capture = Signal()
        self.comb += [
            do_capture.eq(
                txn_arriving &
                ~fifo_full &
                ~overflow_reg
            ),
        ]

        # Overflow register - sticky until clear
        self.sync += [
            If(self.clear,
                overflow_reg.eq(0),
            ).Elif(txn_dropped,
                overflow_reg.eq(1),
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

        # Export count to status interface (truncate to 8 bits)
        self.comb += self.count.eq(count[:8])
