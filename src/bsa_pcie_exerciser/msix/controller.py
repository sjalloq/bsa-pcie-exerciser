#
# LitePCIe MSI-X Controller
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Generates MSI-X Memory Write TLPs based on table entries.
# Supports both software-triggered (BSA testing) and hardware IRQs.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect.csr import *

from litepcie.common import *

from .table import LitePCIeMSIXTable, LitePCIeMSIXPBA


class LitePCIeMSIXController(LiteXModule):
    """
    MSI-X Controller - generates Memory Write TLPs for interrupts.
    
    Reads table entry, checks mask, issues TLP if unmasked.
    
    Parameters
    ----------
    endpoint : LitePCIeMultiBAREndpoint
        Endpoint to get master port from.
        
    table : LitePCIeMSIXTable
        MSI-X table module (for reading entries).
        
    pba : LitePCIeMSIXPBA
        MSI-X PBA module (for pending bit management).
        
    n_irqs : int
        Number of hardware IRQ inputs (directly mapped to vectors 0..n_irqs-1).
    """
    
    def __init__(self, endpoint, table, pba, n_irqs=32):
        self.table = table
        self.pba   = pba
        
        # =====================================================================
        # Trigger Interface
        # =====================================================================
        
        # Software trigger (from BAR0 CSR)
        self.sw_vector = Signal(11)         # Which vector to trigger
        self.sw_valid  = Signal()           # Pulse to trigger
        
        # Hardware IRQs (directly connected, active-high pulses)
        self.irqs = Signal(n_irqs)
        
        # =====================================================================
        # Master Port (for TLP generation)
        # =====================================================================
        
        self.port = port = endpoint.crossbar.get_master_port()
        
        # =====================================================================
        # IRQ Aggregation
        # =====================================================================
        
        # Pending hardware IRQs (sticky until serviced)
        hw_pending = Signal(n_irqs)
        hw_clear   = Signal(n_irqs)
        
        self.sync += hw_pending.eq((hw_pending | self.irqs) & ~hw_clear)
        
        # Priority encoder for hardware IRQs
        hw_irq_valid  = Signal()
        hw_irq_vector = Signal(11)
        
        for i in reversed(range(n_irqs)):
            self.comb += If(hw_pending[i],
                hw_irq_valid.eq(1),
                hw_irq_vector.eq(i),
            )
        
        # =====================================================================
        # Vector Selection
        # =====================================================================
        
        # Software trigger has priority over hardware
        trigger_valid  = Signal()
        trigger_vector = Signal(11)
        trigger_is_sw  = Signal()
        
        self.comb += [
            If(self.sw_valid,
                trigger_valid.eq(1),
                trigger_vector.eq(self.sw_vector),
                trigger_is_sw.eq(1),
            ).Elif(hw_irq_valid,
                trigger_valid.eq(1),
                trigger_vector.eq(hw_irq_vector),
                trigger_is_sw.eq(0),
            ),
        ]
        
        # =====================================================================
        # FSM
        # =====================================================================
        
        self.fsm = fsm = FSM(reset_state="IDLE")
        
        # Latched vector info
        current_vector = Signal(11)
        current_is_sw  = Signal()
        
        fsm.act("IDLE",
            If(trigger_valid,
                NextValue(current_vector, trigger_vector),
                NextValue(current_is_sw, trigger_is_sw),
                # Start table read
                table.vector_num.eq(trigger_vector),
                table.read_en.eq(1),
                NextState("READ-TABLE"),
            ),
        )
        
        fsm.act("READ-TABLE",
            table.vector_num.eq(current_vector),
            If(table.read_valid,
                # Table entry is now available
                If(table.masked,
                    # Vector is masked - set pending bit and return
                    pba.vector_num.eq(current_vector),
                    pba.set_pending.eq(1),
                    NextState("IDLE"),
                ).Else(
                    # Vector not masked - issue TLP
                    NextState("ISSUE-WRITE"),
                ),
            ),
        )
        
        # TLP Memory Write generation
        self.comb += [
            port.source.channel.eq(port.channel),
            port.source.first.eq(1),
            port.source.last.eq(1),
            port.source.we.eq(1),
            port.source.adr.eq(table.msg_addr),
            port.source.req_id.eq(endpoint.phy.id),
            port.source.tag.eq(0),
            port.source.len.eq(1),  # 1 DWORD
            port.source.dat.eq(table.msg_data),
        ]
        
        fsm.act("ISSUE-WRITE",
            port.source.valid.eq(1),
            If(port.source.ready,
                # Clear pending bit (if it was set)
                pba.vector_num.eq(current_vector),
                pba.clear_pending.eq(1),
                # Clear hardware IRQ pending (if hardware triggered)
                If(~current_is_sw & (current_vector < n_irqs),
                    hw_clear.eq(1 << current_vector),
                ),
                NextState("IDLE"),
            ),
        )
        
        # Unused completion sink (we only send posted writes)
        self.comb += port.sink.ready.eq(1)


class LitePCIeMSITrigger(LiteXModule):
    """
    CSR interface for software to trigger MSI-X vectors.
    
    Used for BSA compliance testing where software needs to
    trigger arbitrary interrupt vectors.
    """
    
    def __init__(self):
        # CSR interface
        self.control = CSRStorage(32, fields=[
            CSRField("vector",  size=11, offset=0,
                description="MSI-X vector to trigger (0-2047)"),
            CSRField("trigger", size=1,  offset=15,
                description="Write 1 to trigger the specified vector"),
        ])
        
        self.status = CSRStatus(32, fields=[
            CSRField("busy", size=1, offset=0,
                description="Controller is processing a trigger"),
        ])
        
        # Output to controller
        self.trigger_vector = Signal(11)
        self.trigger_valid  = Signal()
        
        # Input from controller
        self.busy = Signal()
        
        # # #
        
        # Edge detect on trigger bit
        trigger_prev = Signal()
        self.sync += trigger_prev.eq(self.control.fields.trigger)
        
        self.comb += [
            self.trigger_vector.eq(self.control.fields.vector),
            self.trigger_valid.eq(self.control.fields.trigger & ~trigger_prev),
            self.status.fields.busy.eq(self.busy),
        ]


class LitePCIeMSIX(LiteXModule):
    """
    Complete MSI-X subsystem for BSA Exerciser.
    
    Integrates:
    - Table handler (BAR2)
    - PBA handler (BAR5)
    - Controller (TLP generation)
    - Trigger CSR (BAR0)
    
    Parameters
    ----------
    endpoint : LitePCIeMultiBAREndpoint
        The multi-BAR endpoint.
        
    n_vectors : int
        Number of MSI-X vectors (default 2048).
        
    n_irqs : int
        Number of hardware IRQ inputs (default 32).
    """
    
    def __init__(self, endpoint, n_vectors=2048, n_irqs=32):
        data_width = endpoint.data_width
        phy        = endpoint.phy

        # =====================================================================
        # Submodules
        # =====================================================================

        self.table = LitePCIeMSIXTable(
            phy        = phy,
            data_width = data_width,
            n_vectors  = n_vectors,
        )

        self.pba = LitePCIeMSIXPBA(
            phy        = phy,
            data_width = data_width,
            n_vectors  = n_vectors,
        )
        
        self.controller = LitePCIeMSIXController(
            endpoint = endpoint,
            table    = self.table,
            pba      = self.pba,
            n_irqs   = n_irqs,
        )
        
        self.trigger = LitePCIeMSITrigger()
        
        # =====================================================================
        # Connections
        # =====================================================================
        
        # Connect trigger CSR to controller
        self.comb += [
            self.controller.sw_vector.eq(self.trigger.trigger_vector),
            self.controller.sw_valid.eq(self.trigger.trigger_valid),
            self.trigger.busy.eq(~self.controller.fsm.ongoing("IDLE")),
        ]
        
        # =====================================================================
        # External Interfaces
        # =====================================================================
        
        # BAR2 interface (for table)
        self.bar2_req_sink   = self.table.req_sink
        self.bar2_cpl_source = self.table.cpl_source
        
        # BAR5 interface (for PBA)
        self.bar5_req_sink   = self.pba.req_sink
        self.bar5_cpl_source = self.pba.cpl_source
        
        # Hardware IRQs
        self.irqs = self.controller.irqs
