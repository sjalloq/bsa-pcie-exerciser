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

from migen import Module, Signal, Replicate, Instance, ClockSignal, ResetSignal, Memory

class _BSADMABufferMigen(Module):
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

class _BSADMABufferXPM(Module):
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

    Uses Xilinx XPM True Dual Port memory instead of relying on memory inference.
    The Verilog netlist output by Migen's Memory primitive does not synthesize
    correctly as true dual port RAM.

    """

    def __init__(self, size=16*1024, data_width=64):
        self.size = size
        self.data_width = data_width

        bytes_per_word = data_width // 8
        n_entries = size // bytes_per_word
        addr_width = (n_entries - 1).bit_length()

        # Port A Interface (DMA Engine)
        self.a_adr   = Signal(addr_width)
        self.a_dat_w = Signal(data_width)
        self.a_dat_r = Signal(data_width)
        self.a_we    = Signal()
        self.a_re    = Signal()

        # Port B Interface (BAR1 Handler)
        self.b_adr   = Signal(addr_width)
        self.b_dat_w = Signal(data_width)
        self.b_dat_r = Signal(data_width)
        self.b_we    = Signal(bytes_per_word)
        self.b_re    = Signal()

        # Active-high enables
        a_en = Signal()
        b_en = Signal()
        self.comb += [
            a_en.eq(self.a_we | self.a_re),
            b_en.eq((self.b_we != 0) | self.b_re),
        ]

        # Expand port A write enable to byte granularity
        a_we_byte = Signal(bytes_per_word)
        self.comb += a_we_byte.eq(Replicate(self.a_we, bytes_per_word))

        self.specials += Instance("xpm_memory_tdpram",
            # Common parameters
            p_MEMORY_SIZE             = size * 8,  # Total bits
            p_MEMORY_PRIMITIVE        = "block",
            p_CLOCKING_MODE           = "common_clock",
            p_ECC_MODE                = "no_ecc",
            p_MEMORY_INIT_FILE        = "none",
            p_MEMORY_INIT_PARAM       = "0",
            p_USE_MEM_INIT            = 1,
            p_USE_MEM_INIT_MMI        = 0,
            p_WAKEUP_TIME             = "disable_sleep",
            p_AUTO_SLEEP_TIME         = 0,
            p_MESSAGE_CONTROL         = 0,
            p_USE_EMBEDDED_CONSTRAINT = 0,
            p_CASCADE_HEIGHT          = 0,
            p_SIM_ASSERT_CHK          = 0,
            p_WRITE_DATA_WIDTH_A      = data_width,
            p_READ_DATA_WIDTH_A       = data_width,
            p_BYTE_WRITE_WIDTH_A      = 8,
            p_ADDR_WIDTH_A            = addr_width,
            p_READ_RESET_VALUE_A      = "0",
            p_READ_LATENCY_A          = 1,
            p_WRITE_MODE_A            = "read_first",
            p_RST_MODE_A              = "SYNC",
            p_WRITE_DATA_WIDTH_B      = data_width,
            p_READ_DATA_WIDTH_B       = data_width,
            p_BYTE_WRITE_WIDTH_B      = 8,
            p_ADDR_WIDTH_B            = addr_width,
            p_READ_RESET_VALUE_B      = "0",
            p_READ_LATENCY_B          = 1,
            p_WRITE_MODE_B            = "read_first",
            p_RST_MODE_B              = "SYNC",

            # Port A (DMA)
            i_clka   = ClockSignal("sys"),
            i_rsta   = ResetSignal("sys"),
            i_ena    = a_en,
            i_regcea = 1,
            i_wea    = a_we_byte,
            i_addra  = self.a_adr,
            i_dina   = self.a_dat_w,
            o_douta  = self.a_dat_r,

            # Port B (BAR1)
            i_clkb   = ClockSignal("sys"),
            i_rstb   = ResetSignal("sys"),
            i_enb    = b_en,
            i_regceb = 1,
            i_web    = self.b_we,
            i_addrb  = self.b_adr,
            i_dinb   = self.b_dat_w,
            o_doutb  = self.b_dat_r,

            # Unused
            o_sbiterra = Signal(),
            o_dbiterra = Signal(),
            o_sbiterrb = Signal(),
            o_dbiterrb = Signal(),
            i_injectdbiterra = 0,
            i_injectsbiterra = 0,
            i_injectdbiterrb = 0,
            i_injectsbiterrb = 0,
            i_sleep = 0,
        )


def BSADMABuffer(size=16*1024, data_width=64, simulation=False):
    """
    Factory function for DMA buffer.

    Returns the appropriate implementation based on target:
    - simulation=True:  Migen Memory (works with Verilator/iverilog)
    - simulation=False: Xilinx XPM TDPRAM (required for synthesis)

    Parameters
    ----------
    size : int
        Buffer size in bytes (default 16KB).

    data_width : int
        Data width in bits (default 64).

    simulation : bool
        If True, use Migen Memory for simulation.
        If False, use Xilinx XPM for synthesis.

    Returns
    -------
    Module
        Either _BSADMABufferMigen or _BSADMABufferXPM instance.
    """
    if simulation:
        return _BSADMABufferMigen(size, data_width)
    else:
        return _BSADMABufferXPM(size, data_width)
