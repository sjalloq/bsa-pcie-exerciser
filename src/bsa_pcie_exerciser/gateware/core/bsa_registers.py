#
# BSA PCIe Exerciser - BAR0 Register Module
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Implements the ARM BSA Exerciser BAR0 register map per:
# external/sysarch-acs/docs/pcie/Exerciser.md
#
# Uses a Wishbone slave interface for explicit address control.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect import wishbone


# ARM BSA Exerciser identification
EXERCISER_VENDOR_ID = 0x13B5
EXERCISER_DEVICE_ID = 0xED01
EXERCISER_COMBINED_ID = (EXERCISER_DEVICE_ID << 16) | EXERCISER_VENDOR_ID


# Register offsets (byte addresses)
REG_MSICTL         = 0x000
REG_INTXCTL        = 0x004
REG_DMACTL         = 0x008
REG_DMA_OFFSET     = 0x00C
REG_DMA_BUS_ADDR_LO = 0x010
REG_DMA_BUS_ADDR_HI = 0x014
REG_DMA_LEN        = 0x018
REG_DMASTATUS      = 0x01C
REG_PASID_VAL      = 0x020
REG_ATSCTL         = 0x024
REG_ATS_ADDR_LO    = 0x028
REG_ATS_ADDR_HI    = 0x02C
REG_ATS_RANGE_SIZE = 0x030
REG_ATS_PERM       = 0x038
REG_RID_CTL        = 0x03C
REG_TXN_TRACE      = 0x040
REG_TXN_CTRL       = 0x044
REG_ID             = 0x048

# USB Monitor registers (Squirrel/CaptainDMA only)
REG_USB_MON_CTRL        = 0x080
REG_USB_MON_STATUS      = 0x084
REG_USB_MON_RX_CAPTURED = 0x088
REG_USB_MON_RX_DROPPED  = 0x08C
REG_USB_MON_TX_CAPTURED = 0x090
REG_USB_MON_TX_DROPPED  = 0x094
REG_USB_MON_RX_TRUNCATED = 0x098
REG_USB_MON_TX_TRUNCATED = 0x09C


class BSARegisters(LiteXModule):
    """
    ARM BSA Exerciser BAR0 Register Map.

    Implements the register interface defined in the ARM sysarch-acs
    Exerciser specification using a Wishbone slave for explicit
    address control.
    """

    def __init__(self):
        # Wishbone slave interface
        self.bus = wishbone.Interface(data_width=32, adr_width=30, addressing="byte")

        # =====================================================================
        # Register Storage
        # =====================================================================

        # R/W Registers (directly addressable storage)
        self.msictl         = Signal(32, reset=0)
        self.intxctl        = Signal(32, reset=0)
        self.dmactl         = Signal(32, reset=0)
        self.dma_offset     = Signal(32, reset=0)
        self.dma_bus_addr_lo = Signal(32, reset=0)
        self.dma_bus_addr_hi = Signal(32, reset=0)
        self.dma_len        = Signal(32, reset=0)
        self.dmastatus      = Signal(32, reset=0)
        self.pasid_val      = Signal(32, reset=0)
        self.atsctl         = Signal(32, reset=0)
        self.rid_ctl        = Signal(32, reset=0)
        self.txn_ctrl       = Signal(32, reset=0)

        # Read-Only Registers (directly updated by hardware)
        self.ats_addr_lo    = Signal(32, reset=0)
        self.ats_addr_hi    = Signal(32, reset=0)
        self.ats_range_size = Signal(32, reset=0)
        self.ats_perm       = Signal(32, reset=0)
        self.txn_trace      = Signal(32, reset=0xFFFFFFFF)
        self.id             = Signal(32, reset=EXERCISER_COMBINED_ID)

        # =====================================================================
        # Interface Signals (directly usable by other modules)
        # =====================================================================

        # MSI-X interface
        self.msi_trigger = Signal()
        self.msi_vector  = Signal(11)
        self.msi_busy    = Signal()

        # Legacy interrupt interface
        self.intx_assert = Signal()

        # DMA interface
        self.dma_trigger     = Signal()
        self.dma_direction   = Signal()
        self.dma_no_snoop    = Signal()
        self.dma_pasid_en    = Signal()
        self.dma_privileged  = Signal()
        self.dma_instruction = Signal()
        self.dma_use_atc     = Signal()
        self.dma_addr_type   = Signal(2)
        self.dma_bus_addr    = Signal(64)
        self.dma_busy        = Signal()

        # DMA status interface (directly modifiable by DMA engine)
        self.dma_status    = Signal(2)
        self.dma_status_we = Signal()

        # ATS interface (control outputs)
        self.ats_trigger    = Signal()       # Pulse to trigger ATS request
        self.ats_privileged = Signal()       # Privileged access mode
        self.ats_no_write   = Signal()       # Read-only permission requested
        self.ats_pasid_en   = Signal()       # Include PASID in ATS request
        self.ats_exec_req   = Signal()       # Execute permission requested
        self.ats_clear_atc  = Signal()       # Clear ATC (write-1-to-clear)

        # ATS interface (status inputs from ATS engine)
        self.ats_in_flight   = Signal()      # ATS request in progress
        self.ats_success     = Signal()      # Translation successful
        self.ats_cacheable   = Signal()      # Translation result cacheable
        self.ats_invalidated = Signal()      # ATC was invalidated

        # ATS result interface (written by ATS engine)
        self.ats_addr_lo_we    = Signal()    # Write enable for result registers
        self.ats_addr_lo_in    = Signal(32)
        self.ats_addr_hi_in    = Signal(32)
        self.ats_range_size_in = Signal(32)
        self.ats_perm_in       = Signal(32)

        # RID override interface
        self.rid_override_valid = Signal()
        self.rid_override_value = Signal(16)

        # Transaction monitor interface
        self.txn_enable     = Signal()      # Output: enable capture
        self.txn_clear      = Signal()      # Output: clear FIFO (pulse)
        self.txn_overflow   = Signal()      # Input: overflow occurred (sticky)
        self.txn_count      = Signal(8)     # Input: transaction count in FIFO
        self.txn_fifo_data  = Signal(32)
        self.txn_fifo_valid = Signal()
        self.txn_fifo_read  = Signal()

        # USB Monitor interface (Squirrel/CaptainDMA only)
        self.usb_mon_ctrl         = Signal(32, reset=3)  # R/W control register
        self.usb_mon_rx_enable    = Signal()             # Output: enable RX capture
        self.usb_mon_tx_enable    = Signal()             # Output: enable TX capture
        self.usb_mon_clear_stats  = Signal()             # Output: clear statistics (pulse)
        self.usb_mon_rx_captured  = Signal(32)           # Input: RX packets captured
        self.usb_mon_rx_dropped   = Signal(32)           # Input: RX packets dropped
        self.usb_mon_tx_captured  = Signal(32)           # Input: TX packets captured
        self.usb_mon_tx_dropped   = Signal(32)           # Input: TX packets dropped
        self.usb_mon_rx_truncated = Signal(32)           # Input: RX packets truncated
        self.usb_mon_tx_truncated = Signal(32)           # Input: TX packets truncated

        # =====================================================================
        # Wishbone Address Decoding
        # =====================================================================

        # Extract word-aligned address (drop lower 2 bits for 32-bit regs)
        reg_addr = Signal(10)
        self.comb += reg_addr.eq(self.bus.adr[:10] & 0x3FC)  # Mask to register offsets

        # TXN_CTRL composed read value:
        # [0]=enable, [1]=0 (clear is W1C), [2]=overflow (RO), [15:8]=count (RO)
        txn_ctrl_read = Signal(32)
        self.comb += txn_ctrl_read.eq(Cat(
            self.txn_ctrl[0],           # [0] = enable
            Constant(0, 1),             # [1] = clear (always reads 0)
            self.txn_overflow,          # [2] = overflow (RO)
            Constant(0, 5),             # [7:3] = reserved
            self.txn_count,             # [15:8] = count (RO)
            Constant(0, 16),            # [31:16] = reserved
        ))

        # Read data mux
        read_data = Signal(32)
        self.comb += [
            Case(reg_addr, {
                REG_MSICTL:         read_data.eq(self.msictl),
                REG_INTXCTL:        read_data.eq(self.intxctl),
                REG_DMACTL:         read_data.eq(self.dmactl),
                REG_DMA_OFFSET:     read_data.eq(self.dma_offset),
                REG_DMA_BUS_ADDR_LO: read_data.eq(self.dma_bus_addr_lo),
                REG_DMA_BUS_ADDR_HI: read_data.eq(self.dma_bus_addr_hi),
                REG_DMA_LEN:        read_data.eq(self.dma_len),
                REG_DMASTATUS:      read_data.eq(self.dmastatus),
                REG_PASID_VAL:      read_data.eq(self.pasid_val),
                REG_ATSCTL:         read_data.eq(self.atsctl),
                REG_ATS_ADDR_LO:    read_data.eq(self.ats_addr_lo),
                REG_ATS_ADDR_HI:    read_data.eq(self.ats_addr_hi),
                REG_ATS_RANGE_SIZE: read_data.eq(self.ats_range_size),
                REG_ATS_PERM:       read_data.eq(self.ats_perm),
                REG_RID_CTL:        read_data.eq(self.rid_ctl),
                REG_TXN_TRACE:      read_data.eq(self.txn_trace),
                REG_TXN_CTRL:       read_data.eq(txn_ctrl_read),
                REG_ID:             read_data.eq(self.id),
                # USB Monitor registers
                REG_USB_MON_CTRL:        read_data.eq(self.usb_mon_ctrl),
                REG_USB_MON_STATUS:      read_data.eq(0),  # Reserved for overflow flags
                REG_USB_MON_RX_CAPTURED: read_data.eq(self.usb_mon_rx_captured),
                REG_USB_MON_RX_DROPPED:  read_data.eq(self.usb_mon_rx_dropped),
                REG_USB_MON_TX_CAPTURED: read_data.eq(self.usb_mon_tx_captured),
                REG_USB_MON_TX_DROPPED:  read_data.eq(self.usb_mon_tx_dropped),
                REG_USB_MON_RX_TRUNCATED: read_data.eq(self.usb_mon_rx_truncated),
                REG_USB_MON_TX_TRUNCATED: read_data.eq(self.usb_mon_tx_truncated),
                "default":          read_data.eq(0),
            }),
        ]

        # =====================================================================
        # Wishbone Bus Logic
        # =====================================================================

        self.sync += [
            self.bus.ack.eq(0),
            If(self.bus.cyc & self.bus.stb & ~self.bus.ack,
                self.bus.ack.eq(1),
                self.bus.dat_r.eq(read_data),
                If(self.bus.we,
                    Case(reg_addr, {
                        REG_MSICTL:         self.msictl.eq(self.bus.dat_w),
                        REG_INTXCTL:        self.intxctl.eq(self.bus.dat_w),
                        REG_DMACTL:         self.dmactl.eq(self.bus.dat_w),
                        REG_DMA_OFFSET:     self.dma_offset.eq(self.bus.dat_w),
                        REG_DMA_BUS_ADDR_LO: self.dma_bus_addr_lo.eq(self.bus.dat_w),
                        REG_DMA_BUS_ADDR_HI: self.dma_bus_addr_hi.eq(self.bus.dat_w),
                        REG_DMA_LEN:        self.dma_len.eq(self.bus.dat_w),
                        REG_DMASTATUS:      self.dmastatus.eq(self.bus.dat_w),
                        REG_PASID_VAL:      self.pasid_val.eq(self.bus.dat_w),
                        REG_ATSCTL:         self.atsctl.eq(self.bus.dat_w),
                        REG_RID_CTL:        self.rid_ctl.eq(self.bus.dat_w),
                        REG_TXN_CTRL:       self.txn_ctrl.eq(self.bus.dat_w),
                        REG_USB_MON_CTRL:   self.usb_mon_ctrl.eq(self.bus.dat_w),
                        # Read-only registers ignore writes
                    }),
                ),
            ),
        ]

        # =====================================================================
        # Register Field Extraction & Interface Logic
        # =====================================================================

        # MSICTL: [10:0]=vector_id, [31]=trigger
        self.comb += [
            self.msi_vector.eq(self.msictl[:11]),
        ]

        # MSI trigger: detect write with trigger bit set
        msi_trigger_prev = Signal()
        self.sync += msi_trigger_prev.eq(self.msictl[31])
        self.comb += self.msi_trigger.eq(self.msictl[31] & ~msi_trigger_prev & ~self.msi_busy)

        # Auto-clear trigger bit after MSI sent
        self.sync += [
            If(self.msi_trigger,
                self.msictl[31].eq(0),
            ),
        ]

        # INTXCTL: [0]=assert
        self.comb += self.intx_assert.eq(self.intxctl[0])

        # DMACTL: [3:0]=trigger, [4]=dir, [5]=no_snoop, [6]=pasid_en,
        #         [7]=privileged, [8]=instruction, [9]=use_atc, [11:10]=addr_type
        # (per ARM BSA Exerciser spec)
        self.comb += [
            self.dma_direction.eq(self.dmactl[4]),
            self.dma_no_snoop.eq(self.dmactl[5]),
            self.dma_pasid_en.eq(self.dmactl[6]),
            self.dma_privileged.eq(self.dmactl[7]),
            self.dma_instruction.eq(self.dmactl[8]),
            self.dma_use_atc.eq(self.dmactl[9]),
            self.dma_addr_type.eq(self.dmactl[10:12]),
            self.dma_bus_addr.eq(Cat(self.dma_bus_addr_lo, self.dma_bus_addr_hi)),
        ]

        # DMA trigger: detect rising edge on trigger bit (bit 0) when not busy
        # The trigger bit can be set along with other control bits (e.g., direction)
        dma_trigger_prev = Signal()
        self.sync += dma_trigger_prev.eq(self.dmactl[0])
        self.comb += self.dma_trigger.eq(self.dmactl[0] & ~dma_trigger_prev & ~self.dma_busy)

        # Auto-clear DMA trigger bit after started
        self.sync += [
            If(self.dma_trigger,
                self.dmactl[0].eq(0),
            ),
        ]

        # DMA status update from engine
        self.sync += [
            If(self.dma_status_we,
                self.dmastatus[:2].eq(self.dma_status),
            ),
            # Clear on write to bit 2
            If(self.bus.cyc & self.bus.stb & self.bus.we & (reg_addr == REG_DMASTATUS) & self.bus.dat_w[2],
                self.dmastatus[:2].eq(0),
            ),
        ]

        # ATSCTL: [0]=trigger, [1]=privileged, [2]=no_write, [3]=pasid_en,
        #         [4]=exec_req, [5]=clear_atc, [6]=in_flight(RO), [7]=success(RO),
        #         [8]=cacheable(RO), [9]=invalidated(RO)
        self.comb += [
            self.ats_privileged.eq(self.atsctl[1]),
            self.ats_no_write.eq(self.atsctl[2]),
            self.ats_pasid_en.eq(self.atsctl[3]),
            self.ats_exec_req.eq(self.atsctl[4]),
        ]

        # ATS trigger: edge detect on bit 0, only when not in flight
        ats_trigger_prev = Signal()
        self.sync += ats_trigger_prev.eq(self.atsctl[0])
        self.comb += self.ats_trigger.eq(self.atsctl[0] & ~ats_trigger_prev & ~self.ats_in_flight)

        # Auto-clear ATS trigger after started
        self.sync += [
            If(self.ats_trigger,
                self.atsctl[0].eq(0),
            ),
        ]

        # ATS clear_atc: write-1-to-clear
        self.comb += self.ats_clear_atc.eq(self.atsctl[5])
        self.sync += [
            If(self.atsctl[5],
                self.atsctl[5].eq(0),
            ),
        ]

        # Update ATSCTL read-only status bits from ATS engine
        self.sync += [
            self.atsctl[6].eq(self.ats_in_flight),
            self.atsctl[7].eq(self.ats_success),
            self.atsctl[8].eq(self.ats_cacheable),
        ]

        # Invalidated bit (9) is sticky: set on pulse, cleared by writing 1 to bit 9
        self.sync += [
            If(self.ats_invalidated,
                # Set when invalidation occurs
                self.atsctl[9].eq(1),
            ).Elif(self.bus.cyc & self.bus.stb & self.bus.we &
                   (reg_addr == REG_ATSCTL) & self.bus.dat_w[9],
                # Clear when software writes 1 to bit 9 (write-1-to-clear)
                self.atsctl[9].eq(0),
            ),
        ]

        # ATS result registers update from ATS engine
        self.sync += [
            If(self.ats_addr_lo_we,
                self.ats_addr_lo.eq(self.ats_addr_lo_in),
                self.ats_addr_hi.eq(self.ats_addr_hi_in),
                self.ats_range_size.eq(self.ats_range_size_in),
                self.ats_perm.eq(self.ats_perm_in),
            ),
        ]

        # RID_CTL: [15:0]=req_id, [31]=valid
        self.comb += [
            self.rid_override_value.eq(self.rid_ctl[:16]),
            self.rid_override_valid.eq(self.rid_ctl[31]),
        ]

        # TXN_CTRL: [0]=enable, [1]=clear
        self.comb += [
            self.txn_enable.eq(self.txn_ctrl[0]),
            self.txn_clear.eq(self.txn_ctrl[1]),
        ]

        # Auto-clear TXN clear bit
        self.sync += [
            If(self.txn_ctrl[1],
                self.txn_ctrl[1].eq(0),
            ),
        ]

        # TXN_TRACE: read from FIFO interface
        self.comb += [
            If(self.txn_fifo_valid,
                self.txn_trace.eq(self.txn_fifo_data),
            ).Else(
                self.txn_trace.eq(0xFFFFFFFF),
            ),
        ]

        # Generate FIFO read pulse on TXN_TRACE read
        self.comb += [
            self.txn_fifo_read.eq(
                self.bus.cyc & self.bus.stb & ~self.bus.we &
                (reg_addr == REG_TXN_TRACE) & self.bus.ack
            ),
        ]

        # USB_MON_CTRL: [0]=rx_en, [1]=tx_en, [2]=clear_stats
        self.comb += [
            self.usb_mon_rx_enable.eq(self.usb_mon_ctrl[0]),
            self.usb_mon_tx_enable.eq(self.usb_mon_ctrl[1]),
            self.usb_mon_clear_stats.eq(self.usb_mon_ctrl[2]),
        ]

        # Auto-clear USB_MON clear_stats bit
        self.sync += [
            If(self.usb_mon_ctrl[2],
                self.usb_mon_ctrl[2].eq(0),
            ),
        ]
