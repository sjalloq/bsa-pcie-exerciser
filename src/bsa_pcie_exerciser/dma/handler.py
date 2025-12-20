#
# BSA PCIe Exerciser - DMA Buffer Handler (BAR1)
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# BAR1 handler providing PCIe-accessible DMA buffer storage.
# Follows the MSI-X table pattern for TLP handling.
#
# Limitations:
#   - Read requests support len=1 (32-bit) and len=2 (64-bit) only.
#   - Multi-DWORD reads (len>2) are not supported; completion returns
#     only the first 1-2 DWORDs.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import stream

from litepcie.common import *


class BSADMABufferHandler(LiteXModule):
    """
    BAR1 handler for DMA buffer access.

    Provides PCIe slave interface for host read/write access to the
    internal DMA buffer. The host can:
    - Pre-load data before DMA writes to host memory
    - Read back data after DMA reads from host memory
    - Directly inspect/modify buffer contents for debugging

    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance (for completer ID).

    buffer : BSADMABuffer
        The dual-port RAM buffer instance.

    data_width : int
        PCIe data width (64, 128, etc.)
    """

    def __init__(self, phy, buffer, data_width):
        assert data_width >= 64, "Minimum 64-bit data width"

        self.phy = phy
        self.buffer = buffer
        self.data_width = data_width

        # =====================================================================
        # PCIe Slave Interface (from BAR dispatcher)
        # =====================================================================

        self.req_sink   = req_sink   = stream.Endpoint(request_layout(data_width))
        self.cpl_source = cpl_source = stream.Endpoint(completion_layout(data_width))

        # =====================================================================
        # Buffer Port B Access
        # =====================================================================

        # Calculate address width based on buffer size
        bytes_per_word = buffer.data_width // 8
        n_entries = buffer.size // bytes_per_word
        addr_width = (n_entries - 1).bit_length()

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
        qword_idx = Signal(addr_width)

        # Read data register
        read_data = Signal(64)

        # ---------------------------------------------------------------------
        # Write data path - common signals
        # ---------------------------------------------------------------------

        # Combine first_be and last_be into 8-bit byte enable for 64-bit memory
        # first_be: bytes 0-3 (lower DWORD)
        # last_be: bytes 4-7 (upper DWORD)
        write_be = Signal(8)
        self.comb += [
            write_be.eq(Cat(req_sink.first_be, req_sink.last_be)),
            buffer.b_dat_w.eq(req_sink.dat),
        ]

        # ---------------------------------------------------------------------
        # IDLE State: Wait for request
        # ---------------------------------------------------------------------

        fsm.act("IDLE",
            req_sink.ready.eq(1),
            buffer.b_adr.eq(req_sink.adr[3:3+addr_width]),  # Live address for first beat

            If(req_sink.valid & req_sink.first,
                # Capture request header
                NextValue(req_adr, req_sink.adr),
                NextValue(req_len, req_sink.len),
                NextValue(req_tag, req_sink.tag),
                NextValue(req_req_id, req_sink.req_id),
                NextValue(req_we, req_sink.we),
                NextValue(qword_idx, req_sink.adr[3:3+addr_width]),  # QWORD index

                If(req_sink.we,
                    # Write request - perform write on this beat
                    buffer.b_we.eq(write_be),
                    NextState("WRITE"),
                ).Else(
                    # Read request - start memory read
                    NextState("READ_ADDR"),
                ),
            ),
        )

        # ---------------------------------------------------------------------
        # WRITE State: Handle continuation data (multi-beat writes)
        # ---------------------------------------------------------------------

        fsm.act("WRITE",
            req_sink.ready.eq(1),
            buffer.b_adr.eq(qword_idx),  # Captured address for continuation

            If(req_sink.valid,
                # Perform write
                buffer.b_we.eq(write_be),
                # Update QWORD index for multi-beat writes
                NextValue(qword_idx, qword_idx + 1),

                If(req_sink.last,
                    # Write complete
                    NextState("IDLE"),
                ),
            ).Else(
                # No valid data - single-beat TLP already completed, return to IDLE
                NextState("IDLE"),
            ),
        )

        # ---------------------------------------------------------------------
        # READ_ADDR State: Present address to memory
        # ---------------------------------------------------------------------
        #
        # Memory has 1-cycle read latency (registered address). This state
        # presents the address; data will be valid on the next cycle.

        fsm.act("READ_ADDR",
            buffer.b_adr.eq(qword_idx),
            buffer.b_we.eq(0),  # No write during read
            NextState("READ_DATA"),
        )

        # ---------------------------------------------------------------------
        # READ_DATA State: Capture memory read data
        # ---------------------------------------------------------------------

        fsm.act("READ_DATA",
            buffer.b_adr.eq(qword_idx),  # Hold address stable
            buffer.b_we.eq(0),  # No write during read
            NextState("COMPLETE"),
        )

        # Capture read data when transitioning out of READ_DATA
        self.sync += [
            If(fsm.ongoing("READ_DATA"),
                read_data.eq(buffer.b_dat_r),
            ),
        ]

        # ---------------------------------------------------------------------
        # COMPLETE State: Send completion TLP
        # ---------------------------------------------------------------------
        #
        # For len=1 (32-bit): return upper or lower DWORD based on addr[2]
        # For len=2 (64-bit): return full QWORD
        # For len>2: only first 2 DWORDs returned (limitation documented above)

        # Select DWORD for len=1 reads based on addr[2]
        cpl_data = Signal(64)
        self.comb += [
            If(req_len == 1,
                # Single DWORD - select based on addr[2]
                If(req_adr[2],
                    # Upper DWORD requested (offset 0x4 within QWORD)
                    cpl_data.eq(read_data[32:64]),
                ).Else(
                    # Lower DWORD requested (offset 0x0 within QWORD)
                    cpl_data.eq(read_data[0:32]),
                ),
            ).Else(
                # Full QWORD (len >= 2)
                cpl_data.eq(read_data),
            ),
        ]

        fsm.act("COMPLETE",
            buffer.b_we.eq(0),  # No write during completion
            cpl_source.valid.eq(1),
            cpl_source.first.eq(1),
            cpl_source.last.eq(1),
            cpl_source.dat.eq(cpl_data),
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
