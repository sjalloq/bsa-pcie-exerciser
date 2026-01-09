#!/usr/bin/env python3
#
# BSA PCIe Exerciser - Base SoC
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Uses modified LitePCIe fork with bar_hit extraction and attr passthrough.
#

from migen import *
from litex.gen import *

from litex.soc.integration.soc import SoCRegion
from litex.soc.integration.soc_core import SoCMini

from litepcie.phy.s7pciephy import S7PCIEPHY
from litepcie.frontend.wishbone import LitePCIeWishboneBridge

from bsa_pcie_exerciser.gateware.core import (
    LitePCIeBARDispatcher,
    LitePCIeCompletionArbiter,
    LitePCIeMasterArbiter,
    LitePCIeStubBARHandler,
    LitePCIeMultiBAREndpoint,
    BSARegisters,
    INTxController,
)

from bsa_pcie_exerciser.gateware.dma import (
    BSADMABuffer,
    BSADMABufferHandler,
    BSADMAEngine,
)

from bsa_pcie_exerciser.gateware.monitor import TransactionMonitor

from bsa_pcie_exerciser.gateware.msix import (
    LitePCIeMSIXTable,
    LitePCIeMSIXPBA,
    LitePCIeMSIXController,
)

from bsa_pcie_exerciser.gateware.config import BSAConfigSpace, USER_EXT_CFG_DWORD_BASE
from bsa_pcie_exerciser.gateware.pasid import PASIDPrefixInjector
from bsa_pcie_exerciser.gateware.ats import ATSEngine, ATC, ATSInvalidationHandler


class BSAExerciserSoC(SoCMini):
    """
    BSA PCIe Exerciser SoC with multi-BAR configuration.

    BAR Layout:
    - BAR0: CSR registers (4KB)
    - BAR1: DMA buffer (16KB)
    - BAR2: MSI-X table (32KB, 2048 vectors)
    - BAR5: MSI-X PBA (4KB)
    """

    MSIX_VECTORS = 16

    mem_map = {
        "csr": 0x8000_0000,
    }

    def __init__(
        self,
        platform,
        sys_clk_freq=125e6,
        crg_cls=None,
        pcie_phy=None,
        simulation=False,
        pcie_gt_locn="X0Y0",
    ):
        """
        Initialize BSA Exerciser SoC.

        Args:
            platform: LiteX platform
            sys_clk_freq: System clock frequency
            crg_cls: CRG class to instantiate (required for hardware builds)
            pcie_phy: Optional PHY instance (for simulation with PHYStub)
            simulation: If True, skip hardware-specific initialization (CRG, LTSSM)
            pcie_gt_locn: PCIe GTP channel location suffix (eg. X0Y0, X0Y2)
        """

        # SoCMini -----------------------------------------------------------------
        SoCMini.__init__(self, platform,
            clk_freq      = sys_clk_freq,
            ident         = "BSA PCIe Exerciser",
            ident_version = True,
        )

        # CRG ---------------------------------------------------------------------
        # Skip CRG in simulation - clock domains provided externally
        if not simulation:
            if crg_cls is None:
                raise ValueError("crg_cls is required for hardware builds (non-simulation)")
            self.crg = crg_cls(platform, sys_clk_freq)

        # PCIe PHY ----------------------------------------------------------------
        # Use provided PHY (for simulation) or create S7PCIEPHY (for hardware)
        if pcie_phy is not None:
            self.pcie_phy = pcie_phy
        else:
            # Prevent LiteX inserting BUFGs on the outputs from the MMCM as they cause pulse width timing violations.
            self.pcie_phy = S7PCIEPHY(platform, platform.request("pcie_x1"), mmcm_clk125_buf=None, mmcm_clk250_buf=None)

            # Multi-BAR Configuration ---------------------------------------------
            # Configure the Xilinx PCIe IP for multiple BARs and Device ID
            self.pcie_phy.update_config({
                # Device Identification (ARM BSA Exerciser)
                "Vendor_ID"          : "13B5",   # ARM Ltd.
                "Device_ID"          : "ED01",   # BSA Exerciser
                "Revision_ID"        : "01",
                "Subsystem_Vendor_ID": "13B5",
                "Subsystem_ID"       : "ED01",

                # BAR0: CSRs (4KB)
                "Bar0_Enabled"      : True,
                "Bar0_Scale"        : "Kilobytes",
                "Bar0_Size"         : 4,
                "Bar0_Type"         : "Memory",
                "Bar0_Prefetchable" : "false",

                # BAR1: DMA Buffer (16KB)
                "Bar1_Enabled"      : True,
                "Bar1_Scale"        : "Kilobytes",
                "Bar1_Size"         : 16,
                "Bar1_Type"         : "Memory",
                "Bar1_Prefetchable" : "false",

                # BAR2: MSI-X Table (sized for 2048 vectors, 16 used)
                "Bar2_Enabled"      : True,
                "Bar2_Scale"        : "Kilobytes",
                "Bar2_Size"         : 32,
                "Bar2_Type"         : "Memory",
                "Bar2_Prefetchable" : "false",  # MSI-X must be non-prefetchable

                # BAR3/4: Disabled
                "Bar3_Enabled"      : False,
                "Bar4_Enabled"      : False,

                # BAR5: MSI-X PBA (4KB)
                "Bar5_Enabled"      : True,
                "Bar5_Scale"        : "Kilobytes",
                "Bar5_Size"         : 4,
                "Bar5_Type"         : "Memory",
                "Bar5_Prefetchable" : "false",  # MSI-X must be non-prefetchable

                # MSI-X Configuration (16 vectors)
                "MSI_Enabled"       : False,  # Disable legacy MSI
                "MSIx_Enabled"      : True,
                "MSIx_Table_Size"   : "0F",   # 16 vectors (N-1 encoding, hex)
                "MSIx_Table_BIR"    : "BAR_2",
                "MSIx_Table_Offset" : "0",
                "MSIx_PBA_BIR"      : "BAR_5",
                "MSIx_PBA_Offset"   : "0",

                # Legacy Interrupts
                "Legacy_Interrupt": "INTA",
                "IntX_Generation" : True,

                # AER Capability (required for ACS error-injection tests)
                "AER_Enabled"                  : True,
                "AER_ECRC_Check_Capable"       : False,
                "AER_ECRC_Gen_Capable"         : False,
                "AER_Multiheader"              : False,
                "AER_Permit_Root_Error_Update" : False,

                # User-defined extended configuration space (ACS capabilities/DVSEC)
                "EXT_PCI_CFG_Space"            : True,
                "EXT_PCI_CFG_Space_Addr"       : f"{USER_EXT_CFG_DWORD_BASE:X}",

            })

            # LTSSM Tracer for link debugging
            self.pcie_phy.add_ltssm_tracer()

            # GTP channel location (reset from .xci, then set explicitly)
            pcie_gt_loc = f"GTPE2_CHANNEL_{pcie_gt_locn}"
            platform.toolchain.pre_placement_commands.append(
                "reset_property LOC [get_cells -hierarchical -filter {{NAME=~pcie_s7/*gtp_channel.gtpe2_channel_i}}]"
            )
            platform.toolchain.pre_placement_commands.append(
                f"set_property LOC {pcie_gt_loc} "
                "[get_cells -hierarchical -filter {{NAME=~pcie_s7/*gtp_channel.gtpe2_channel_i}}]"
            )

        # DMA Buffer and Handler --------------------------------------------------
        self.dma_buffer = BSADMABuffer(
            size       = 16*1024,  # 16KB buffer
            data_width = self.pcie_phy.data_width,
            simulation = simulation,  # Use Migen memory in simulation
        )

        self.dma_handler = BSADMABufferHandler(
            phy        = self.pcie_phy,
            buffer     = self.dma_buffer,
            data_width = self.pcie_phy.data_width,
        )

        # MSI-X Table and PBA Handlers --------------------------------------------
        self.msix_table = LitePCIeMSIXTable(
            phy        = self.pcie_phy,
            data_width = self.pcie_phy.data_width,
            n_vectors  = self.MSIX_VECTORS,
        )

        self.msix_pba = LitePCIeMSIXPBA(
            phy        = self.pcie_phy,
            data_width = self.pcie_phy.data_width,
            n_vectors  = self.MSIX_VECTORS,
        )

        # PASID Prefix Injector ---------------------------------------------------
        # Create early so it can be passed to endpoint; PASID signals connected later
        self.pasid_injector = PASIDPrefixInjector(
            data_width = self.pcie_phy.data_width,
        )

        # ATS Invalidation Handler ------------------------------------------------
        # Create early so msg_source can be passed to endpoint for TX arbitration
        # Signal connections are done later after other components are created
        self.ats_invalidation = ATSInvalidationHandler(
            phy        = self.pcie_phy,
            data_width = self.pcie_phy.data_width,
        )

        # PCIe Endpoint -----------------------------------------------------------
        self.pcie_endpoint = LitePCIeMultiBAREndpoint(self.pcie_phy,
            endianness           = "big",
            max_pending_requests = 4,
            bar_enables  = {0: True, 1: True, 2: True, 3: False, 4: False, 5: True},
            bar_handlers = {
                1: self.dma_handler,   # BAR1: DMA buffer
                2: self.msix_table,    # BAR2: MSI-X Table
                5: self.msix_pba,      # BAR5: MSI-X PBA
            },
            tx_filter    = self.pasid_injector,
            with_ats_inv = True,  # Enable ATS Invalidation Message handling
            with_configuration = True,  # Tap configuration requests for TXN_TRACE
            # Raw TX sources: Message TLPs that bypass packetizer (e.g., ATS Inv Completion)
            raw_tx_sources = [self.ats_invalidation.msg_source],
        )

        # BSA Registers -----------------------------------------------------------
        # Full ARM BSA Exerciser register set with explicit address mapping
        self.bsa_regs = BSARegisters()
        self.bus.add_slave(
            name="bsa_regs",
            slave=self.bsa_regs.bus,
            region=SoCRegion(origin=0x0, size=0x1000, cached=False),
        )

        # Wishbone Bridge (BAR0 -> BSA Registers) ---------------------------------
        # Connect PCIe Wishbone bridge directly to BSA registers
        # base_address=0 makes BAR0 offset directly map to bsa_regs (at origin=0x0)
        self.pcie_bridge = LitePCIeWishboneBridge(self.pcie_endpoint,
            base_address = 0x0,
        )
        self.bus.add_master("pcie", master=self.pcie_bridge.wishbone)

        # User-defined extended config space responder ---------------------------
        self.config_space = BSAConfigSpace(self.pcie_endpoint, self.pcie_phy)


        # DMA Engine --------------------------------------------------------------
        self.dma_engine = BSADMAEngine(
            phy              = self.pcie_phy,
            buffer           = self.dma_buffer,
            data_width       = self.pcie_phy.data_width,
            max_request_size = 128,
        )

        # Connect DMA engine control signals to BSA registers
        self.comb += [
            self.dma_engine.trigger.eq(self.bsa_regs.dma_trigger),
            self.dma_engine.direction.eq(self.bsa_regs.dma_direction),
            self.dma_engine.no_snoop.eq(self.bsa_regs.dma_no_snoop),
            self.dma_engine.addr_type.eq(self.bsa_regs.dma_addr_type),
            self.dma_engine.bus_addr.eq(self.bsa_regs.dma_bus_addr),
            self.dma_engine.length.eq(self.bsa_regs.dma_len),
            self.dma_engine.offset.eq(self.bsa_regs.dma_offset),
            # PASID control signals
            self.dma_engine.pasid_en.eq(self.bsa_regs.dma_pasid_en),
            self.dma_engine.pasid_val.eq(self.bsa_regs.pasid_val[:20]),
            self.dma_engine.privileged.eq(self.bsa_regs.dma_privileged),
            self.dma_engine.instruction.eq(self.bsa_regs.dma_instruction),
            # Requester ID override (for ACS testing - BSA e001)
            self.dma_engine.rid_override_valid.eq(self.bsa_regs.rid_override_valid),
            self.dma_engine.rid_override_value.eq(self.bsa_regs.rid_override_value),
        ]

        # Poison mode handling (BAR0/BAR1).
        self.comb += [
            self.bsa_regs.poison_mode.eq(self.config_space.poison_mode),
            self.dma_handler.poison_mode.eq(self.config_space.poison_mode),
        ]

        # Connect DMA engine status signals to BSA registers
        self.comb += [
            self.bsa_regs.dma_busy.eq(self.dma_engine.busy),
            self.bsa_regs.dma_status.eq(self.dma_engine.status),
            self.bsa_regs.dma_status_we.eq(self.dma_engine.status_we),
        ]

        # Connect DMA engine to master port for TLP requests
        # Note: LitePCIeMasterPort swaps sink/source internally:
        #   dma_port.source = internal port's sink (request_layout)
        #   dma_port.sink   = internal port's source (completion_layout)
        dma_port = self.pcie_endpoint.crossbar.get_master_port()
        self.comb += [
            self.dma_engine.source.connect(dma_port.source),  # request -> request
            dma_port.sink.connect(self.dma_engine.sink),      # completion -> completion
        ]

        # MSI-X Controller ----------------------------------------------------------
        self.msix_controller = LitePCIeMSIXController(
            endpoint = self.pcie_endpoint,
            table    = self.msix_table,
            pba      = self.msix_pba,
        )

        # Connect MSI-X controller to BSA registers
        self.comb += [
            self.msix_controller.sw_vector.eq(self.bsa_regs.msi_vector),
            self.msix_controller.sw_valid.eq(self.bsa_regs.msi_trigger),
            self.bsa_regs.msi_busy.eq(~self.msix_controller.fsm.ongoing("IDLE")),
        ]

        # Error injection to PCIe core ------------------------------------------------
        poison_event = Signal()
        inject_pulse = Signal()
        err_code     = Signal(11)

        self.comb += [
            poison_event.eq(self.bsa_regs.poison_event | self.dma_handler.poison_event),
            inject_pulse.eq(self.config_space.inject_error_pulse | poison_event),
            err_code.eq(Mux(poison_event, 0x0A, self.config_space.error_code)),
        ]

        # Default all error inputs to 0.
        self.comb += [
            self.pcie_phy.cfg_err_ecrc.eq(0),
            self.pcie_phy.cfg_err_ur.eq(0),
            self.pcie_phy.cfg_err_cpl_timeout.eq(0),
            self.pcie_phy.cfg_err_cpl_unexpect.eq(0),
            self.pcie_phy.cfg_err_cpl_abort.eq(0),
            self.pcie_phy.cfg_err_posted.eq(0),
            self.pcie_phy.cfg_err_cor.eq(0),
            self.pcie_phy.cfg_err_atomic_egress_blocked.eq(0),
            self.pcie_phy.cfg_err_internal_cor.eq(0),
            self.pcie_phy.cfg_err_malformed.eq(0),
            self.pcie_phy.cfg_err_mc_blocked.eq(0),
            self.pcie_phy.cfg_err_poisoned.eq(0),
            self.pcie_phy.cfg_err_norecovery.eq(0),
            self.pcie_phy.cfg_err_tlp_cpl_header.eq(0),
            self.pcie_phy.cfg_err_locked.eq(0),
            self.pcie_phy.cfg_err_acs.eq(0),
            self.pcie_phy.cfg_err_internal_uncor.eq(0),
            self.pcie_phy.cfg_err_aer_headerlog.eq(0),
        ]

        # Drive error reporting pulses on inject_pulse.
        self.comb += If(inject_pulse,
            Case(err_code, {
                # Correctable errors.
                0x0: self.pcie_phy.cfg_err_cor.eq(1),
                0x1: self.pcie_phy.cfg_err_cor.eq(1),
                0x2: self.pcie_phy.cfg_err_cor.eq(1),
                0x3: self.pcie_phy.cfg_err_cor.eq(1),
                0x4: self.pcie_phy.cfg_err_cor.eq(1),
                0x5: self.pcie_phy.cfg_err_cor.eq(1),
                0x6: self.pcie_phy.cfg_err_internal_cor.eq(1),
                0x7: self.pcie_phy.cfg_err_cor.eq(1),
                # Uncorrectable errors.
                0x8: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                0x9: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                0xA: self.pcie_phy.cfg_err_poisoned.eq(1),
                0xB: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                0xC: self.pcie_phy.cfg_err_cpl_timeout.eq(1),
                0xD: self.pcie_phy.cfg_err_cpl_abort.eq(1),
                0xE: self.pcie_phy.cfg_err_cpl_unexpect.eq(1),
                0xF: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                0x10: self.pcie_phy.cfg_err_malformed.eq(1),
                0x11: self.pcie_phy.cfg_err_ecrc.eq(1),
                0x12: self.pcie_phy.cfg_err_ur.eq(1),
                0x13: self.pcie_phy.cfg_err_acs.eq(1),
                0x14: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                0x15: self.pcie_phy.cfg_err_mc_blocked.eq(1),
                0x16: self.pcie_phy.cfg_err_atomic_egress_blocked.eq(1),
                0x17: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                0x18: self.pcie_phy.cfg_err_internal_uncor.eq(1),
                "default": self.pcie_phy.cfg_err_internal_uncor.eq(1),
            }),
        )

        # ATS Engine and ATC --------------------------------------------------------
        self.atc = ATC()
        # Get master port first to obtain channel for completion routing
        ats_port = self.pcie_endpoint.crossbar.get_master_port()
        self.ats_engine = ATSEngine(
            phy        = self.pcie_phy,
            data_width = self.pcie_phy.data_width,
            channel    = ats_port.channel,
        )

        # NOTE: PASID signals now travel through the stream (phy_layout).
        # The prefix injector reads pasid_en, pasid_val, privileged, execute
        # from the stream itself. No external mux needed.
        # DMA/ATS engines set these on their source.pasid_* signals.

        # Connect ATS engine control signals from BSA registers
        self.comb += [
            self.ats_engine.trigger.eq(self.bsa_regs.ats_trigger & self.config_space.ats_enable),
            self.ats_engine.address.eq(self.bsa_regs.dma_bus_addr),  # Shared address register
            self.ats_engine.pasid_en.eq(self.bsa_regs.ats_pasid_en),
            self.ats_engine.pasid_val.eq(self.bsa_regs.pasid_val[:20]),
            self.ats_engine.privileged.eq(self.bsa_regs.ats_privileged),
            self.ats_engine.no_write.eq(self.bsa_regs.ats_no_write),
            self.ats_engine.exec_req.eq(self.bsa_regs.ats_exec_req),
            self.ats_engine.clear_atc.eq(self.bsa_regs.ats_clear_atc),
        ]

        # Clear ATC and ATS results when ATS is disabled.
        ats_enable_prev = Signal()
        self.sync += [
            ats_enable_prev.eq(self.config_space.ats_enable),
        ]
        ats_disable_pulse = Signal()
        self.comb += ats_disable_pulse.eq(ats_enable_prev & ~self.config_space.ats_enable)
        self.comb += [
            self.bsa_regs.ats_clear_results.eq(ats_disable_pulse),
        ]

        # Connect ATS engine status signals to BSA registers
        self.comb += [
            self.bsa_regs.ats_in_flight.eq(self.ats_engine.in_flight),
            self.bsa_regs.ats_success.eq(self.ats_engine.success),
            self.bsa_regs.ats_cacheable.eq(self.ats_engine.cacheable),
            # Note: ats_invalidated connected below after invalidation handler is created
        ]

        # Connect ATS engine results to BSA registers
        self.comb += [
            self.bsa_regs.ats_addr_lo_we.eq(self.ats_engine.result_we),
            self.bsa_regs.ats_addr_lo_in.eq(self.ats_engine.translated_addr[:32]),
            self.bsa_regs.ats_addr_hi_in.eq(self.ats_engine.translated_addr[32:]),
            self.bsa_regs.ats_range_size_in.eq(self.ats_engine.range_size),
            self.bsa_regs.ats_perm_in.eq(self.ats_engine.permissions),
        ]

        # Connect ATS engine to ATC for storing translations
        self.comb += [
            self.atc.store.eq(self.ats_engine.result_we & self.ats_engine.success),
            self.atc.store_input_addr.eq(self.ats_engine.address),
            self.atc.store_output_addr.eq(self.ats_engine.translated_addr),
            self.atc.store_range_size.eq(self.ats_engine.range_size),
            self.atc.store_permissions.eq(self.ats_engine.permissions),
            self.atc.store_pasid_valid.eq(self.ats_engine.pasid_en),
            self.atc.store_pasid_val.eq(self.ats_engine.pasid_val),
            # Note: atc.invalidate connected below after invalidation handler is created
        ]

        # Connect DMA engine to ATC for address translation lookup
        # Use the ATC's proper PASID-aware lookup interface instead of internal signals
        self.comb += [
            # Drive ATC lookup inputs from DMA engine
            self.atc.lookup_addr.eq(self.dma_engine.lookup_addr),
            self.atc.lookup_pasid_valid.eq(self.dma_engine.pasid_out_en),
            self.atc.lookup_pasid_val.eq(self.dma_engine.pasid_out_val),

            # Connect ATC lookup results to DMA engine
            self.dma_engine.atc_hit.eq(self.atc.lookup_hit),
            self.dma_engine.atc_output_addr.eq(self.atc.lookup_output),
            self.dma_engine.use_atc.eq(self.bsa_regs.dma_use_atc & self.config_space.ats_enable),
        ]

        # Connect ATS engine to master port for TLP requests
        self.comb += [
            self.ats_engine.source.connect(ats_port.source),
            ats_port.sink.connect(self.ats_engine.sink),
        ]

        # ATS Invalidation Handler Connections --------------------------------------
        # (Handler created earlier so msg_source can be passed to endpoint)

        # Connect invalidation handler to ATC
        self.comb += [
            self.ats_invalidation.atc_valid.eq(self.atc.valid),
            self.ats_invalidation.atc_input_addr.eq(self.atc._input_addr),
            self.ats_invalidation.atc_input_addr_end.eq(self.atc._input_addr_end),
            self.ats_invalidation.atc_range_size.eq(self.atc._range_size),
            self.ats_invalidation.atc_pasid_valid.eq(self.atc._pasid_valid),
            self.ats_invalidation.atc_pasid_val.eq(self.atc._pasid_val),
        ]

        # Connect invalidation handler to ATS engine
        self.comb += [
            self.ats_invalidation.ats_in_flight.eq(self.ats_engine.in_flight),
            self.ats_engine.retry.eq(self.ats_invalidation.ats_retry),
        ]

        # Connect invalidation handler to DMA engine
        self.comb += [
            self.ats_invalidation.dma_busy.eq(self.dma_engine.busy),
            self.ats_invalidation.dma_using_atc.eq(self.bsa_regs.dma_use_atc),
        ]

        # ATC invalidation can come from software (clear_atc) or invalidation handler
        self.comb += self.atc.invalidate.eq(
            self.bsa_regs.ats_clear_atc | ats_disable_pulse | self.ats_invalidation.atc_invalidate
        )

        # Update invalidated status from both sources
        self.comb += self.bsa_regs.ats_invalidated.eq(self.atc.invalidated)

        # Connect invalidation handler RX path to depacketizer ATS_INV source
        self.comb += self.pcie_endpoint.ats_inv_source.connect(
            self.ats_invalidation.inv_sink
        )

        # Transaction Monitor -------------------------------------------------------
        self.txn_monitor = TransactionMonitor(
            data_width = self.pcie_phy.data_width,
            fifo_depth = 32,  # BSA spec maximum
        )

        # Tap into the request stream from depacketizer
        req_source = self.pcie_endpoint.req_source
        self.comb += [
            self.txn_monitor.tap_valid.eq(req_source.valid & req_source.ready),
            self.txn_monitor.tap_first.eq(req_source.first),
            self.txn_monitor.tap_last.eq(req_source.last),
            self.txn_monitor.tap_we.eq(req_source.we),
            self.txn_monitor.tap_adr.eq(req_source.adr),
            self.txn_monitor.tap_len.eq(req_source.len),
            self.txn_monitor.tap_dat.eq(req_source.dat),
            self.txn_monitor.tap_req_id.eq(req_source.req_id),
            self.txn_monitor.tap_tag.eq(req_source.tag),
            self.txn_monitor.tap_bar_hit.eq(req_source.bar_hit),
        ]

        # Conditionally connect optional fields (attr, at, first_be, last_be)
        if hasattr(req_source, 'first_be'):
            self.comb += self.txn_monitor.tap_first_be.eq(req_source.first_be)
        if hasattr(req_source, 'last_be'):
            self.comb += self.txn_monitor.tap_last_be.eq(req_source.last_be)
        if hasattr(req_source, 'attr'):
            self.comb += self.txn_monitor.tap_attr.eq(req_source.attr)
        if hasattr(req_source, 'at'):
            self.comb += self.txn_monitor.tap_at.eq(req_source.at)

        # Tap configuration requests for TXN_TRACE
        conf_source = self.pcie_endpoint.conf_source
        self.comb += [
            self.txn_monitor.tap_cfg_valid.eq(conf_source.valid & conf_source.ready),
            self.txn_monitor.tap_cfg_first.eq(conf_source.first),
            self.txn_monitor.tap_cfg_last.eq(conf_source.last),
            self.txn_monitor.tap_cfg_we.eq(conf_source.we),
            self.txn_monitor.tap_cfg_type.eq(conf_source.cfg_type),
            self.txn_monitor.tap_cfg_bus_number.eq(conf_source.bus_number),
            self.txn_monitor.tap_cfg_device_no.eq(conf_source.device_no),
            self.txn_monitor.tap_cfg_func.eq(conf_source.func),
            self.txn_monitor.tap_cfg_ext_reg.eq(conf_source.ext_reg),
            self.txn_monitor.tap_cfg_register_no.eq(conf_source.register_no),
            self.txn_monitor.tap_cfg_tag.eq(conf_source.tag),
            self.txn_monitor.tap_cfg_first_be.eq(conf_source.first_be),
            self.txn_monitor.tap_cfg_dat.eq(conf_source.dat),
        ]

        # Connect monitor control/status to BSA registers
        self.comb += [
            self.txn_monitor.enable.eq(self.bsa_regs.txn_enable),
            self.txn_monitor.clear.eq(self.bsa_regs.txn_clear),
            self.bsa_regs.txn_fifo_data.eq(self.txn_monitor.fifo_data),
            self.bsa_regs.txn_fifo_valid.eq(~self.txn_monitor.fifo_empty),
            self.txn_monitor.fifo_read.eq(self.bsa_regs.txn_fifo_read),
            # Overflow and count status
            self.bsa_regs.txn_overflow.eq(self.txn_monitor.overflow),
            self.bsa_regs.txn_count.eq(self.txn_monitor.count),
        ]

        # Legacy INTx Controller ---------------------------------------------------
        self.intx_ctrl = INTxController()
        self.comb += self.intx_ctrl.source.connect(self.pcie_phy.intx)
        self.comb += self.intx_ctrl.intx_assert.eq(self.bsa_regs.intx_assert)

        # Vivado ------------------------------------------------------------------
        platform.toolchain.pre_synthesis_commands.append("set_property XPM_LIBRARIES XPM_MEMORY [current_project]")
