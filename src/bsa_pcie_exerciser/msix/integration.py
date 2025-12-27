#
# MSI-X Integration for BSA Exerciser
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Shows how to integrate the MSI-X subsystem with the multi-BAR endpoint.
#

from migen import *
from litex.gen import *

from litepcie.common import *
from litepcie.core.crossbar import LitePCIeCrossbar
from litepcie.tlp.depacketizer import LitePCIeTLPDepacketizer
from litepcie.tlp.packetizer import LitePCIeTLPPacketizer

from ..core import (
    LitePCIeBARDispatcher,
    LitePCIeCompletionArbiter,
    LitePCIeMasterArbiter,
    LitePCIeStubBARHandler,
)
from .table import LitePCIeMSIXTable, LitePCIeMSIXPBA
from .controller import LitePCIeMSIXController, LitePCIeMSITrigger


class LitePCIeMultiBAREndpointMSIX(LiteXModule):
    """
    PCIe Endpoint with multi-BAR routing and integrated MSI-X.
    
    BAR Layout:
    - BAR0: User crossbar (CSRs, DMA, etc.)
    - BAR1: User crossbar (optional, e.g., DMA buffer)
    - BAR2: MSI-X Table (32KB for 2048 vectors)
    - BAR5: MSI-X PBA (4KB)
    
    Parameters
    ----------
    phy : S7PCIEPHY (or compatible)
        PCIe PHY instance.
        
    endianness : str
        Endianness for TLP processing.
        
    address_width : int
        Address width for crossbar ports.
        
    max_pending_requests : int
        Maximum outstanding requests per crossbar.
        
    n_vectors : int
        Number of MSI-X vectors (default 2048).

    with_bar1 : bool
        Enable BAR1 crossbar (default False).
    """
    
    def __init__(self, phy,
                 endianness="big",
                 address_width=32,
                 max_pending_requests=4,
                 n_vectors=2048,
                 with_bar1=False):
        
        self.phy        = phy
        self.data_width = phy.data_width
        
        # =====================================================================
        # TLP Depacketizer / Packetizer
        # =====================================================================
        
        self.depacketizer = LitePCIeTLPDepacketizer(
            data_width   = phy.data_width,
            endianness   = endianness,
            address_mask = phy.bar0_mask,
            capabilities = ["REQUEST", "COMPLETION"],
        )
        
        self.packetizer = LitePCIeTLPPacketizer(
            data_width    = phy.data_width,
            endianness    = endianness,
            address_width = address_width,
            capabilities  = ["REQUEST", "COMPLETION"],
        )
        
        # Connect PHY
        self.comb += [
            phy.source.connect(self.depacketizer.sink),
            self.packetizer.source.connect(phy.sink),
        ]
        
        # =====================================================================
        # BAR0 Crossbar (User: CSRs, DMA control, etc.)
        # =====================================================================
        
        self.bar0_crossbar = bar0_crossbar = LitePCIeCrossbar(
            data_width           = phy.data_width,
            address_width        = address_width,
            max_pending_requests = max_pending_requests,
            cmp_bufs_buffered    = True,
        )
        
        # Expose as 'crossbar' for compatibility with existing frontends
        self.crossbar  = bar0_crossbar
        self.crossbars = {0: bar0_crossbar}
        
        # =====================================================================
        # BAR1 Crossbar (Optional: DMA buffer, etc.)
        # =====================================================================
        
        if with_bar1:
            self.bar1_crossbar = bar1_crossbar = LitePCIeCrossbar(
                data_width           = phy.data_width,
                address_width        = address_width,
                max_pending_requests = max_pending_requests,
                cmp_bufs_buffered    = True,
            )
            self.crossbars[1] = bar1_crossbar
        
        # =====================================================================
        # MSI-X Subsystem (BAR2 + BAR5)
        # =====================================================================
        
        self.msix_table = msix_table = LitePCIeMSIXTable(
            phy        = phy,
            data_width = phy.data_width,
            n_vectors  = n_vectors,
        )

        self.msix_pba = msix_pba = LitePCIeMSIXPBA(
            phy        = phy,
            data_width = phy.data_width,
            n_vectors  = n_vectors,
        )
        
        self.msix_controller = msix_ctrl = LitePCIeMSIXController(
            endpoint = self,  # Pass self to get master port
            table    = msix_table,
            pba      = msix_pba,
        )
        
        self.msix_trigger = msix_trig = LitePCIeMSITrigger()
        
        # Connect trigger to controller
        self.comb += [
            msix_ctrl.sw_vector.eq(msix_trig.trigger_vector),
            msix_ctrl.sw_valid.eq(msix_trig.trigger_valid),
            msix_trig.busy.eq(~msix_ctrl.fsm.ongoing("IDLE")),
        ]
        
        # =====================================================================
        # Stub Handlers (BAR3, BAR4)
        # =====================================================================
        
        self.bar3_stub = bar3_stub = LitePCIeStubBARHandler(
            data_width = phy.data_width,
            bar_num    = 3,
            return_ur  = True,
        )
        
        self.bar4_stub = bar4_stub = LitePCIeStubBARHandler(
            data_width = phy.data_width,
            bar_num    = 4,
            return_ur  = True,
        )
        
        # =====================================================================
        # Build BAR Routing Tables
        # =====================================================================
        
        bar_req_sinks = {
            0: bar0_crossbar.phy_slave.sink,
            2: msix_table.req_sink,
            3: bar3_stub.req_sink,
            4: bar4_stub.req_sink,
            5: msix_pba.req_sink,
        }
        
        bar_cpl_sources = {
            0: bar0_crossbar.phy_slave.source,
            2: msix_table.cpl_source,
            3: bar3_stub.cpl_source,
            4: bar4_stub.cpl_source,
            5: msix_pba.cpl_source,
        }
        
        bar_master_sources = {
            0: bar0_crossbar.phy_master.source,
        }
        
        bar_master_sinks = {
            0: bar0_crossbar.phy_master.sink,
        }
        
        if with_bar1:
            bar_req_sinks[1]      = bar1_crossbar.phy_slave.sink
            bar_cpl_sources[1]    = bar1_crossbar.phy_slave.source
            bar_master_sources[1] = bar1_crossbar.phy_master.source
            bar_master_sinks[1]   = bar1_crossbar.phy_master.sink
        else:
            bar1_stub = LitePCIeStubBARHandler(
                data_width = phy.data_width,
                bar_num    = 1,
                return_ur  = True,
            )
            self.bar1_stub = bar1_stub
            bar_req_sinks[1]   = bar1_stub.req_sink
            bar_cpl_sources[1] = bar1_stub.cpl_source
        
        # =====================================================================
        # Request Dispatcher
        # =====================================================================
        
        self.bar_dispatcher = LitePCIeBARDispatcher(
            source      = self.depacketizer.req_source,
            bar_sinks   = bar_req_sinks,
            default_bar = 0,
        )
        
        # =====================================================================
        # Completion Arbiter
        # =====================================================================
        
        self.cpl_arbiter = LitePCIeCompletionArbiter(
            bar_sources = bar_cpl_sources,
            sink        = self.packetizer.cmp_sink,
        )
        
        # =====================================================================
        # Master Request Arbiter
        # =====================================================================
        
        self.master_arbiter = LitePCIeMasterArbiter(
            bar_sources = bar_master_sources,
            sink        = self.packetizer.req_sink,
        )
        
        # =====================================================================
        # Master Completion Routing
        # =====================================================================
        
        # Completions need to go to the right BAR based on channel.
        # Broadcast to all master sinks - each crossbar filters by channel internally.
        # Note: .connect() cannot be used multiple times, so use explicit signals.
        
        cmp_source = self.depacketizer.cmp_source
        
        # All sinks see the same data
        for bar_num, sink in bar_master_sinks.items():
            self.comb += [
                sink.valid.eq(cmp_source.valid),
                sink.first.eq(cmp_source.first),
                sink.last.eq(cmp_source.last),
                sink.dat.eq(cmp_source.dat),
                sink.be.eq(cmp_source.be),
                sink.len.eq(cmp_source.len),
                sink.err.eq(cmp_source.err),
                sink.end.eq(cmp_source.end),
                sink.tag.eq(cmp_source.tag),
                sink.adr.eq(cmp_source.adr),
                sink.req_id.eq(cmp_source.req_id),
                sink.cmp_id.eq(cmp_source.cmp_id),
            ]
        
        # Ready when ANY sink accepts (crossbar handles channel filtering)
        from functools import reduce
        from operator import or_
        if len(bar_master_sinks) > 0:
            self.comb += cmp_source.ready.eq(
                reduce(or_, [sink.ready for sink in bar_master_sinks.values()])
            )


# =============================================================================
# Example Top-Level Integration
# =============================================================================

def create_bsa_soc_with_msix(platform, sys_clk_freq=125e6):
    """
    Example showing how to use LitePCIeMultiBAREndpointMSIX.
    
    This is a template - actual integration would be in bsa_pcie_exerciser.py
    """
    from litex.soc.integration.soc_core import SoCMini
    from litepcie.phy.s7pciephy import S7PCIEPHY
    from litepcie.frontend.wishbone import LitePCIeWishboneBridge
    
    class BSAExerciserSoCWithMSIX(SoCMini):
        mem_map = {"csr": 0x0000_0000}
        
        def __init__(self, platform, sys_clk_freq):
            SoCMini.__init__(self, platform,
                clk_freq      = sys_clk_freq,
                ident         = "BSA PCIe Exerciser with MSI-X",
                ident_version = True,
            )
            
            # PCIe PHY with multi-BAR config
            self.pcie_phy = S7PCIEPHY(platform, platform.request("pcie_x1"),
                data_width = 64,
                bar0_size  = 0x1000,
                cd         = "sys",
            )
            
            # Configure BARs and MSI-X in IP
            self.pcie_phy.update_config({
                "Bar0_Enabled": True,  "Bar0_Scale": "Kilobytes", "Bar0_Size": 4,
                "Bar1_Enabled": True,  "Bar1_Scale": "Kilobytes", "Bar1_Size": 16,
                "Bar2_Enabled": True,  "Bar2_Scale": "Kilobytes", "Bar2_Size": 32,
                "Bar3_Enabled": False,
                "Bar4_Enabled": False,
                "Bar5_Enabled": True,  "Bar5_Scale": "Kilobytes", "Bar5_Size": 4,
                
                "MSI_Enabled":       False,
                "MSIx_Enabled":      True,
                "MSIx_Table_Size":   "7FF",     # 2048 vectors
                "MSIx_Table_BIR":    "BAR_2",
                "MSIx_Table_Offset": "0",
                "MSIx_PBA_BIR":      "BAR_5",
                "MSIx_PBA_Offset":   "0",
            })
            
            # Multi-BAR Endpoint with MSI-X
            self.pcie_endpoint = LitePCIeMultiBAREndpointMSIX(self.pcie_phy,
                endianness           = "big",
                max_pending_requests = 4,
                n_vectors            = 2048,
                with_bar1            = False,
            )

            # Wishbone Bridge (BAR0 -> CSRs)
            self.pcie_bridge = LitePCIeWishboneBridge(self.pcie_endpoint,
                base_address = self.mem_map["csr"],
            )
            self.bus.add_master(master=self.pcie_bridge.wishbone)

            # Add MSI-X trigger CSR to BAR0
            # The trigger CSR is part of msix_trigger, needs to be added to CSR bus
            self.msix_trigger = self.pcie_endpoint.msix_trigger

    return BSAExerciserSoCWithMSIX(platform, sys_clk_freq)
