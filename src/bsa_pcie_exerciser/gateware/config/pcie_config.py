#
# BSA PCIe Exerciser - User-Defined Config Space Capabilities
#
# Copyright (c) 2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Provides a minimal extended configuration space responder for ACS-required
# capabilities (ATS, PASID, ACS, DPC, DVSEC Error Injection).
#

from migen import *
from litex.gen import *


# User-defined extended config space base (DWORD address).
# Matches S7PCIEPHY EXT_PCI_CFG_Space_Addr (0x6B -> 0x1AC bytes).
USER_EXT_CFG_DWORD_BASE = 0x6B


# Extended Capability IDs.
ECID_ACS   = 0x000D
ECID_ATS   = 0x000F
ECID_PASID = 0x001B
ECID_DPC   = 0x001D
ECID_DVSEC = 0x0023


class BSAConfigSpace(LiteXModule):
    """
    Minimal user-defined extended configuration space responder.

    Exposes the ACS-required extended capabilities and DVSEC error-injection
    control, backed by simple registers. This is intended to satisfy ACS
    capability discovery on 7-series.
    """

    def __init__(self, pcie_endpoint, phy, base_dword=USER_EXT_CFG_DWORD_BASE, max_pasid_bits=20):
        # Control/status outputs.
        self.ats_ctrl           = Signal(32, reset=0)
        self.pasid_ctrl         = Signal(32, reset=0)
        self.acs_ctrl           = Signal(32, reset=0)
        self.dpc_ctrl           = Signal(32, reset=0)
        self.dpc_status         = Signal(32, reset=0)
        self.dvsec_ctrl         = Signal(32, reset=0)

        self.inject_error_pulse = Signal()
        self.inject_on_dma      = Signal()
        self.poison_mode        = Signal()
        self.error_code         = Signal(11)
        self.error_fatal        = Signal()
        self.ats_enable         = Signal()

        # # #

        # Config/Completion endpoints.
        self.conf_ep = conf_ep = pcie_endpoint.conf_source
        self.comp_ep = comp_ep = pcie_endpoint.crossbar.get_slave_port(
            address_decoder=lambda a: 0
        ).source

        # Capability layout (DWORD offsets relative to base).
        ats_dw    = 0x00  # header @ +0, ctrl @ +1
        pasid_dw  = 0x02  # header @ +2, cap/ctrl @ +3
        acs_dw    = 0x04  # header @ +4, ctrl @ +5
        dpc_dw    = 0x06  # header @ +6, ctrl @ +7, status @ +8
        dvsec_dw  = 0x09  # header @ +9, dvsec hdr @ +10, ctrl @ +11

        # Header helper.
        def ecap_header(ecid, version, next_ptr):
            return (ecid & 0xFFFF) | ((version & 0xF) << 16) | ((next_ptr & 0xFFF) << 20)

        # Absolute BYTE addresses for next pointers.
        ats_next   = (base_dword + pasid_dw) * 4
        pasid_next = (base_dword + acs_dw) * 4
        acs_next   = (base_dword + dpc_dw) * 4
        dpc_next   = (base_dword + dvsec_dw) * 4
        dvsec_next = 0

        # Header DWORDs.
        ats_header   = ecap_header(ECID_ATS, 1, ats_next)
        pasid_header = ecap_header(ECID_PASID, 1, pasid_next)
        acs_header   = ecap_header(ECID_ACS, 1, acs_next)
        dpc_header   = ecap_header(ECID_DPC, 1, dpc_next)
        dvsec_header = ecap_header(ECID_DVSEC, 1, dvsec_next)

        # DVSEC header 1 fields.
        dvsec_vendor_id = 0x13B5
        dvsec_rev = 0
        dvsec_length = 0xC  # bytes
        dvsec_header1 = (
            (dvsec_vendor_id & 0xFFFF) |
            ((dvsec_rev & 0xF) << 16) |
            ((dvsec_length & 0xFFF) << 20)
        )

        # PASID capability register (max width in bits 12:8).
        pasid_max_width = Signal(5, reset=max_pasid_bits)
        self.comb += pasid_max_width.eq(max_pasid_bits)

        # Decode address (DWORD address).
        dw_addr = Signal(10)
        self.comb += dw_addr.eq(Cat(conf_ep.register_no, conf_ep.ext_reg))

        # Byte enable mask for 32-bit writes.
        be_mask = Signal(32)
        self.comb += be_mask.eq(Cat(
            Replicate(conf_ep.first_be[0], 8),
            Replicate(conf_ep.first_be[1], 8),
            Replicate(conf_ep.first_be[2], 8),
            Replicate(conf_ep.first_be[3], 8),
        ))

        # Latched request fields.
        latched_req_id = Signal(16)
        latched_tag    = Signal(8)
        latched_we     = Signal()
        latched_addr   = Signal(9)
        latched_wdata  = Signal(32)
        read_data      = Signal(32)

        # Read mux.
        self.comb += read_data.eq(0)
        self.comb += Case(latched_addr, {
            base_dword + ats_dw    : read_data.eq(ats_header),
            base_dword + ats_dw + 1: read_data.eq(self.ats_ctrl),
            base_dword + pasid_dw  : read_data.eq(pasid_header),
            base_dword + pasid_dw + 1: read_data.eq(self.pasid_ctrl),
            base_dword + acs_dw    : read_data.eq(acs_header),
            base_dword + acs_dw + 1: read_data.eq(self.acs_ctrl),
            base_dword + dpc_dw    : read_data.eq(dpc_header),
            base_dword + dpc_dw + 1: read_data.eq(self.dpc_ctrl),
            base_dword + dpc_dw + 2: read_data.eq(self.dpc_status),
            base_dword + dvsec_dw  : read_data.eq(dvsec_header),
            base_dword + dvsec_dw + 1: read_data.eq(dvsec_header1),
            base_dword + dvsec_dw + 2: read_data.eq(self.dvsec_ctrl),
            "default": read_data.eq(0),
        })

        # FSM for config reads/writes.
        self.fsm = fsm = FSM(reset_state="IDLE")

        fsm.act("IDLE",
            conf_ep.ready.eq(1),
            If(conf_ep.valid,
                NextValue(latched_req_id, conf_ep.req_id),
                NextValue(latched_tag, conf_ep.tag),
                NextValue(latched_we, conf_ep.we),
                NextValue(latched_addr, dw_addr),
                NextValue(latched_wdata, conf_ep.dat[:32]),
                NextState("RESPOND"),
            ),
        )

        fsm.act("RESPOND",
            comp_ep.valid.eq(1),
            comp_ep.first.eq(1),
            comp_ep.last.eq(1),
            comp_ep.tag.eq(latched_tag),
            comp_ep.adr.eq(0),
            comp_ep.cmp_id.eq(phy.id),
            comp_ep.req_id.eq(latched_req_id),
            comp_ep.status.eq(0),
            If(latched_we,
                comp_ep.len.eq(0),
                comp_ep.byte_count.eq(0),
                comp_ep.dat.eq(0),
            ).Else(
                comp_ep.len.eq(1),
                comp_ep.byte_count.eq(4),
                comp_ep.dat.eq(read_data),
            ),
            If(comp_ep.ready,
                NextState("IDLE"),
            ),
        )

        # Write handling.
        self.sync += [
            self.inject_error_pulse.eq(0),
            If(fsm.ongoing("IDLE") & conf_ep.valid & conf_ep.ready & conf_ep.we,
                Case(dw_addr, {
                    base_dword + ats_dw + 1: self.ats_ctrl.eq(
                        (self.ats_ctrl & ~be_mask) | (conf_ep.dat[:32] & be_mask)
                    ),
                    base_dword + pasid_dw + 1: self.pasid_ctrl.eq(
                        (self.pasid_ctrl & ~be_mask) | (conf_ep.dat[:32] & be_mask)
                    ),
                    base_dword + acs_dw + 1: self.acs_ctrl.eq(
                        (self.acs_ctrl & ~be_mask) | (conf_ep.dat[:32] & be_mask)
                    ),
                    base_dword + dpc_dw + 1: self.dpc_ctrl.eq(
                        (self.dpc_ctrl & ~be_mask) | (conf_ep.dat[:32] & be_mask)
                    ),
                    base_dword + dpc_dw + 2: self.dpc_status.eq(
                        self.dpc_status & ~(conf_ep.dat[:32] & be_mask)
                    ),
                    base_dword + dvsec_dw + 2: self.dvsec_ctrl.eq(
                        (self.dvsec_ctrl & ~be_mask) | (conf_ep.dat[:32] & be_mask)
                    ),
                    "default": self.dvsec_ctrl.eq(self.dvsec_ctrl),
                }),
            ),
        ]

        # PASID capability: preserve Max PASID Width bits [12:8].
        self.sync += [
            self.pasid_ctrl[8:13].eq(pasid_max_width),
        ]

        # DVSEC control decode.
        self.comb += [
            self.inject_on_dma.eq(self.dvsec_ctrl[16]),
            self.poison_mode.eq(self.dvsec_ctrl[18]),
            self.error_code.eq(self.dvsec_ctrl[20:31]),
            self.error_fatal.eq(self.dvsec_ctrl[31]),
            self.ats_enable.eq(self.ats_ctrl[31]),
        ]

        # DVSEC ID is fixed to 0x1 (read-only).
        self.sync += [
            self.dvsec_ctrl[:16].eq(0x1),
        ]

        # Auto-clear inject_error_immediately bit and generate pulse.
        self.sync += [
            If(self.dvsec_ctrl[17],
                self.inject_error_pulse.eq(1),
                self.dvsec_ctrl[17].eq(0),
            ),
        ]

        # Update DPC status on injected errors when triggers are enabled.
        self.sync += [
            If(self.inject_error_pulse & (self.dpc_ctrl[16:18] != 0),
                self.dpc_status[0].eq(1),
                self.dpc_status[1:3].eq(Mux(self.error_fatal, 0x2, 0x1)),
                self.dpc_status[16:32].eq(phy.id),
            ),
        ]
