#
# BSA PCIe Exerciser - DMA Buffer
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Dual-port RAM buffer for DMA data storage.
# Port A: DMA engine access (read for writes to host, write for reads from host)
# Port B: BAR1 handler access (host TLP reads/writes)
#

from migen import *
from litex.gen import *


class BSADMABuffer(LiteXModule):
    """
    Dual-port RAM buffer for DMA operations.

    The buffer serves as the internal storage for the BSA exerciser's DMA engine.
    Data is stored here when performing DMA reads from host memory, and sourced
    from here when performing DMA writes to host memory.

    Memory Layout:
        - 16KB buffer (default), 64-bit data width
        - 2048 QWORD entries (64 bits each)
        - Byte-addressable via offset from DMA_OFFSET register

    Port A (DMA Engine):
        - Used by DMA engine FSM
        - Writes when receiving completion data (DMA read from host)
        - Reads when sending write data (DMA write to host)

    Port B (BAR1 Handler):
        - Used by TLP handler for host access
        - Supports read/write from host via BAR1 memory space
        - Byte-granular write enables for partial writes

    Parameters
    ----------
    size : int
        Buffer size in bytes (default 16KB).

    data_width : int
        Data width in bits (default 64).
    """

    def __init__(self, size=16*1024, data_width=64):
        assert data_width >= 64, "Minimum 64-bit data width"
        assert size >= 1024, "Minimum 1KB buffer"

        self.size = size
        self.data_width = data_width

        # Calculate address width: log2(size / (data_width/8))
        bytes_per_word = data_width // 8
        n_entries = size // bytes_per_word
        addr_width = (n_entries - 1).bit_length()

        # =====================================================================
        # Port A Interface (DMA Engine)
        # =====================================================================

        self.a_adr   = Signal(addr_width)          # Word address
        self.a_dat_w = Signal(data_width)          # Write data
        self.a_dat_r = Signal(data_width)          # Read data
        self.a_we    = Signal()                    # Write enable
        self.a_re    = Signal()                    # Read enable

        # =====================================================================
        # Port B Interface (BAR1 Handler)
        # =====================================================================

        self.b_adr   = Signal(addr_width)          # Word address
        self.b_dat_w = Signal(data_width)          # Write data
        self.b_dat_r = Signal(data_width)          # Read data
        self.b_we    = Signal(bytes_per_word)      # Byte-granular write enables
        self.b_re    = Signal()                    # Read enable

        # =====================================================================
        # Memory
        # =====================================================================

        # Initialize memory to zero
        mem_init = [0] * n_entries

        self.specials.mem = mem = Memory(data_width, n_entries, init=mem_init)

        # Port A: DMA engine access (simple write enable)
        self.specials.port_a = port_a = mem.get_port(write_capable=True)

        # Port B: BAR1 handler access (byte-granular write enables)
        self.specials.port_b = port_b = mem.get_port(
            write_capable=True,
            we_granularity=8
        )

        # =====================================================================
        # Port A Connections
        # =====================================================================

        self.comb += [
            port_a.adr.eq(self.a_adr),
            port_a.dat_w.eq(self.a_dat_w),
            self.a_dat_r.eq(port_a.dat_r),
            port_a.we.eq(self.a_we),
        ]

        # =====================================================================
        # Port B Connections
        # =====================================================================

        self.comb += [
            port_b.adr.eq(self.b_adr),
            port_b.dat_w.eq(self.b_dat_w),
            self.b_dat_r.eq(port_b.dat_r),
            port_b.we.eq(self.b_we),
        ]
