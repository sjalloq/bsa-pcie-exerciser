#
# LitePCIe MSI-X Table Handler
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# BAR2 handler providing PCIe-accessible MSI-X table storage.
# Supports up to 2048 vectors (32KB table).
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import *


class LitePCIeMSIXTable(LiteXModule):
    """
    MSI-X Table BAR handler.

    Provides:
    - PCIe slave interface for host read/write access
    - Internal read port for MSI-X controller

    Table Entry Format (16 bytes per vector):
        Offset 0x00: Message Address Low  (32-bit)
        Offset 0x04: Message Address High (32-bit)
        Offset 0x08: Message Data         (32-bit)
        Offset 0x0C: Vector Control       (32-bit, bit 0 = Mask)

    Memory Layout (64-bit words):
        QWORD[N*2 + 0] = {addr_hi, addr_lo}    @ byte offset N*16 + 0x00
        QWORD[N*2 + 1] = {control, msg_data}   @ byte offset N*16 + 0x08

    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance (for completer ID).

    data_width : int
        PCIe data width (64, 128, etc.)

    n_vectors : int
        Number of MSI-X vectors (default 2048).
    """

    def __init__(self, phy, data_width, n_vectors=2048):
        assert data_width >= 64, "Minimum 64-bit data width"
        assert n_vectors <= 2048, "Max 2048 vectors"

        self.phy        = phy
        self.data_width = data_width
        self.n_vectors  = n_vectors

        # =====================================================================
        # PCIe Slave Interface (from BAR dispatcher)
        # =====================================================================

        self.req_sink   = req_sink   = stream.Endpoint(request_layout(data_width))
        self.cpl_source = cpl_source = stream.Endpoint(completion_layout(data_width))

        # =====================================================================
        # Internal Read Port (for MSI-X Controller)
        # =====================================================================

        self.vector_num = Signal(11)           # Input: which vector (0-2047)
        self.read_en    = Signal()             # Input: trigger read
        self.read_valid = Signal()             # Output: data valid
        self.msg_addr   = Signal(64)           # Output: Message Address
        self.msg_data   = Signal(32)           # Output: Message Data
        self.masked     = Signal()             # Output: Vector masked?

        # =====================================================================
        # Table Memory (64-bit wide)
        # =====================================================================

        # Memory organized as 64-bit words with byte-granular write enables
        # 2048 vectors × 2 QWORDs = 4096 entries
        n_entries = n_vectors * 2

        # Initialize memory:
        # QWORD 0: {addr_hi, addr_lo} = 0
        # QWORD 1: {control, msg_data} = {0x00000001, 0x00000000} (masked by default)
        mem_init = []
        for i in range(n_entries):
            if (i % 2) == 1:  # Control/Data QWORD
                # control[31:0] in upper 32 bits, msg_data[31:0] in lower 32 bits
                # Mask bit is bit 32 (bit 0 of control field)
                mem_init.append(0x0000000100000000)  # Masked by default
            else:
                mem_init.append(0x0)

        # Dual-port memory with byte-granular write enables
        self.specials.mem = mem = Memory(64, n_entries, init=mem_init)

        # Port A: PCIe access with byte enables
        self.specials.port_a = port_a = mem.get_port(write_capable=True, we_granularity=8)

        # Port B: Internal read (for controller)
        self.specials.port_b = port_b = mem.get_port(has_re=True)

        # =====================================================================
        # PCIe Access FSM
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # Latched request info
        req_adr    = Signal(32)
        req_len    = Signal(10)
        req_tag    = Signal(8)
        req_req_id = Signal(16)
        req_we     = Signal()

        # Memory addressing: byte address to QWORD index
        # Byte address [14:3] gives QWORD index (bits 0-2 are byte offset within QWORD)
        qword_idx = Signal(12)

        # Read pipeline
        read_data_valid = Signal()
        read_data       = Signal(64)

        # ---------------------------------------------------------------------
        # IDLE State: Wait for request, capture header combinationally
        # ---------------------------------------------------------------------

        fsm.act("IDLE",
            req_sink.ready.eq(1),

            If(req_sink.valid & req_sink.first,
                # Capture request header
                NextValue(req_adr, req_sink.adr),
                NextValue(req_len, req_sink.len),
                NextValue(req_tag, req_sink.tag),
                NextValue(req_req_id, req_sink.req_id),
                NextValue(req_we, req_sink.we),
                NextValue(qword_idx, req_sink.adr[3:15]),  # QWORD index

                If(req_sink.we,
                    # Write request - handle data on this beat
                    NextState("WRITE"),
                ).Else(
                    # Read request - start memory read
                    NextState("READ"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # WRITE State: Handle write data
        # ---------------------------------------------------------------------

        # Combinational write path - write happens when in WRITE state
        # or on first beat of write (IDLE -> WRITE transition)
        write_active = Signal()

        self.comb += [
            write_active.eq(
                (fsm.ongoing("IDLE") & req_sink.valid & req_sink.first & req_sink.we) |
                (fsm.ongoing("WRITE") & req_sink.valid)
            ),

            # Memory write - address from captured value or live value
            If(fsm.ongoing("IDLE"),
                port_a.adr.eq(req_sink.adr[3:15]),
            ).Else(
                port_a.adr.eq(qword_idx),
            ),

            # Byte enables and data come directly from request
            port_a.we.eq(Replicate(write_active, 8) & req_sink.be),
            port_a.dat_w.eq(req_sink.dat),
        ]

        fsm.act("WRITE",
            req_sink.ready.eq(1),

            If(req_sink.valid,
                # Update QWORD index for multi-beat writes
                NextValue(qword_idx, qword_idx + 1),

                If(req_sink.last,
                    # Write complete
                    NextState("IDLE"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # READ State: Issue memory read, wait for data
        # ---------------------------------------------------------------------

        fsm.act("READ",
            # Memory read address
            port_a.adr.eq(qword_idx),

            # Wait one cycle for memory read latency
            NextValue(read_data_valid, 1),
            NextState("COMPLETE"),
        )

        # Capture read data
        self.sync += [
            If(fsm.ongoing("READ"),
                read_data.eq(port_a.dat_r),
            ),
        ]

        # ---------------------------------------------------------------------
        # COMPLETE State: Send completion TLP
        # ---------------------------------------------------------------------

        fsm.act("COMPLETE",
            cpl_source.valid.eq(1),
            cpl_source.first.eq(1),
            cpl_source.last.eq(1),
            cpl_source.dat.eq(read_data),
            cpl_source.len.eq(req_len),
            cpl_source.err.eq(0),
            cpl_source.end.eq(1),
            cpl_source.tag.eq(req_tag),
            cpl_source.adr.eq(req_adr),
            cpl_source.req_id.eq(req_req_id),
            cpl_source.cmp_id.eq(phy.id),

            If(cpl_source.ready,
                NextValue(read_data_valid, 0),
                NextState("IDLE"),
            ),
        )

        # =====================================================================
        # Internal Read Logic (for MSI-X Controller)
        # =====================================================================

        # 3-cycle read: present address, capture first QWORD, capture second QWORD
        # Cycle 0: read_en asserted, present address for QWORD 0
        # Cycle 1: present address for QWORD 1, capture QWORD 0
        # Cycle 2: capture QWORD 1, signal valid

        int_read_cnt    = Signal(2)  # 0-2
        int_read_active = Signal()

        # Latch the read results
        int_qword0 = Signal(64)  # {addr_hi, addr_lo}
        int_qword1 = Signal(64)  # {control, msg_data}

        # Port B addressing: vector_num * 2 + offset
        self.comb += [
            port_b.adr.eq(Cat(int_read_cnt[0], self.vector_num)),
            port_b.re.eq(int_read_active),
        ]

        self.sync += [
            self.read_valid.eq(0),

            If(self.read_en & ~int_read_active,
                int_read_active.eq(1),
                int_read_cnt.eq(0),
            ),

            If(int_read_active,
                int_read_cnt.eq(int_read_cnt + 1),

                Case(int_read_cnt, {
                    1: int_qword0.eq(port_b.dat_r),
                    2: [
                        int_qword1.eq(port_b.dat_r),
                        int_read_active.eq(0),
                        self.read_valid.eq(1),
                    ],
                }),
            ),
        ]

        # Output the latched values
        # QWORD 0: {addr_hi[63:32], addr_lo[31:0]}
        # QWORD 1: {control[63:32], msg_data[31:0]}
        self.comb += [
            self.msg_addr.eq(int_qword0),
            self.msg_data.eq(int_qword1[0:32]),
            self.masked.eq(int_qword1[32]),  # Bit 0 of control field
        ]


class LitePCIeMSIXPBA(LiteXModule):
    """
    MSI-X Pending Bit Array BAR handler.

    Read-only from PCIe perspective (host reads pending status).
    Written internally by MSI-X controller.

    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance (for completer ID).

    data_width : int
        PCIe data width.

    n_vectors : int
        Number of MSI-X vectors (default 2048).
    """

    def __init__(self, phy, data_width, n_vectors=2048):
        self.phy        = phy
        self.data_width = data_width
        self.n_vectors  = n_vectors

        # =====================================================================
        # PCIe Slave Interface
        # =====================================================================

        self.req_sink   = req_sink   = stream.Endpoint(request_layout(data_width))
        self.cpl_source = cpl_source = stream.Endpoint(completion_layout(data_width))

        # =====================================================================
        # Internal Interface (for MSI-X Controller)
        # =====================================================================

        self.set_pending   = Signal()       # Pulse: set pending bit
        self.clear_pending = Signal()       # Pulse: clear pending bit
        self.vector_num    = Signal(11)     # Which vector to modify

        # =====================================================================
        # Pending Bit Storage
        # =====================================================================

        # For 2048 vectors: 2048 bits = 32 QWORDs = 256 bytes
        # Use Array for indexed access
        n_qwords = (n_vectors + 63) // 64
        self.pending = pending = Array([Signal(64, name=f"pba_{i}") for i in range(n_qwords)])

        # Set/clear logic - use indexed access
        qword_idx = Signal(5)
        bit_idx   = Signal(6)

        self.comb += [
            qword_idx.eq(self.vector_num[6:]),
            bit_idx.eq(self.vector_num[:6]),
        ]

        # Single indexed update (more efficient than 32 parallel comparators)
        self.sync += [
            If(self.set_pending,
                pending[qword_idx].eq(pending[qword_idx] | (1 << bit_idx)),
            ),
            If(self.clear_pending,
                pending[qword_idx].eq(pending[qword_idx] & ~(1 << bit_idx)),
            ),
        ]

        # =====================================================================
        # PCIe Access FSM
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # Latched request info
        req_adr    = Signal(32)
        req_len    = Signal(10)
        req_tag    = Signal(8)
        req_req_id = Signal(16)

        # PBA QWORD index from address
        pba_qword_idx = Signal(5)
        read_data     = Signal(64)

        # ---------------------------------------------------------------------
        # IDLE State
        # ---------------------------------------------------------------------

        fsm.act("IDLE",
            req_sink.ready.eq(1),

            If(req_sink.valid & req_sink.first,
                NextValue(req_adr, req_sink.adr),
                NextValue(req_len, req_sink.len),
                NextValue(req_tag, req_sink.tag),
                NextValue(req_req_id, req_sink.req_id),
                NextValue(pba_qword_idx, req_sink.adr[3:8]),

                If(req_sink.we,
                    # Writes are silently ignored (PBA is read-only)
                    If(req_sink.last,
                        NextState("IDLE"),
                    ).Else(
                        NextState("WRITE_IGNORE"),
                    ),
                ).Else(
                    NextState("READ"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # WRITE_IGNORE State: Consume and ignore write data
        # ---------------------------------------------------------------------

        fsm.act("WRITE_IGNORE",
            req_sink.ready.eq(1),

            If(req_sink.valid & req_sink.last,
                NextState("IDLE"),
            ),
        )

        # ---------------------------------------------------------------------
        # READ State: Fetch pending bits
        # ---------------------------------------------------------------------

        fsm.act("READ",
            # Mux the pending bits
            NextValue(read_data, pending[pba_qword_idx]),
            NextState("COMPLETE"),
        )

        # ---------------------------------------------------------------------
        # COMPLETE State: Send completion TLP
        # ---------------------------------------------------------------------

        fsm.act("COMPLETE",
            cpl_source.valid.eq(1),
            cpl_source.first.eq(1),
            cpl_source.last.eq(1),
            cpl_source.dat.eq(read_data),
            cpl_source.len.eq(req_len),
            cpl_source.err.eq(0),
            cpl_source.end.eq(1),
            cpl_source.tag.eq(req_tag),
            cpl_source.adr.eq(req_adr),
            cpl_source.req_id.eq(req_req_id),
            cpl_source.cmp_id.eq(phy.id),

            If(cpl_source.ready,
                NextState("IDLE"),
            ),
        )
