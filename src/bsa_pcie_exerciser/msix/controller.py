#
# LitePCIe MSI-X Controller (Software-Triggered Only)
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Generates MSI-X Memory Write TLPs based on table entries.
# Software-triggered only - sufficient for BSA compliance testing.
#

from migen import *
from litex.gen import *
from litex.soc.interconnect.csr import *

from litepcie.common import *

from .table import LitePCIeMSIXTable, LitePCIeMSIXPBA


class LitePCIeMSIXController(LiteXModule):
    """
    MSI-X Controller - generates Memory Write TLPs for interrupts.

    Software-triggered only. When triggered:
    1. Reads table entry for specified vector
    2. If masked: sets PBA pending bit, returns
    3. If unmasked: issues Memory Write TLP to host

    Parameters
    ----------
    endpoint : LitePCIeMultiBAREndpoint
        Endpoint to get master port from.

    table : LitePCIeMSIXTable
        MSI-X table module (for reading entries).

    pba : LitePCIeMSIXPBA
        MSI-X PBA module (for pending bit management).
    """

    def __init__(self, endpoint, table, pba):
        self.table = table
        self.pba   = pba

        # =====================================================================
        # Software Trigger Interface
        # =====================================================================

        self.sw_vector = Signal(11)         # Which vector to trigger (0-2047)
        self.sw_valid  = Signal()           # Pulse to trigger

        # =====================================================================
        # Master Port (for TLP generation)
        # =====================================================================

        self.port = port = endpoint.crossbar.get_master_port()

        # =====================================================================
        # FSM
        # =====================================================================

        self.fsm = fsm = FSM(reset_state="IDLE")

        # Latched vector
        current_vector = Signal(11)

        fsm.act("IDLE",
            If(self.sw_valid,
                NextValue(current_vector, self.sw_vector),
                # Start table read
                table.vector_num.eq(self.sw_vector),
                table.read_en.eq(1),
                NextState("READ_TABLE"),
            ),
        )

        fsm.act("READ_TABLE",
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
                    NextState("ISSUE_WRITE"),
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

        fsm.act("ISSUE_WRITE",
            port.source.valid.eq(1),
            If(port.source.ready,
                # Clear pending bit (in case it was set previously)
                pba.vector_num.eq(current_vector),
                pba.clear_pending.eq(1),
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

    Usage:
        1. Write vector number to bits [10:0]
        2. Write 1 to trigger bit [15]
        3. Poll busy bit [0] of status register (optional)
        4. Must clear trigger bit before next trigger
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
    """

    def __init__(self, endpoint, n_vectors=2048):
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
