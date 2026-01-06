#
# BSA PCIe Exerciser - Address Translation Cache (ATC)
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# Single-entry Address Translation Cache per BSA Exerciser spec.
# Stores the result of the last successful ATS translation.
#

from migen import *
from litex.gen import *


class ATC(LiteXModule):
    """
    Single-entry Address Translation Cache for BSA Exerciser.

    Per BSA spec, the exerciser stores maximum 1 ATS request result.
    The cache provides lookup for DMA operations using translated addresses.

    Attributes
    ----------
    valid : Signal, out
        Cache entry is valid.

    store : Signal, in
        Store new translation result.

    store_input_addr : Signal(64), in
        Input (untranslated) address for new entry.

    store_output_addr : Signal(64), in
        Output (translated) address for new entry.

    store_range_size : Signal(32), in
        Translation range size in bytes.

    store_permissions : Signal(8), in
        Permission bits.

    store_pasid_valid : Signal, in
        PASID is valid for this entry.

    store_pasid_val : Signal(20), in
        PASID value for this entry.

    lookup_addr : Signal(64), in
        Address to look up in cache.

    lookup_pasid_valid : Signal, in
        PASID is valid for lookup.

    lookup_pasid_val : Signal(20), in
        PASID value for lookup.

    lookup_hit : Signal, out
        Lookup hit (address in range and PASID matches).

    lookup_output : Signal(64), out
        Translated address for hit.

    lookup_perm : Signal(8), out
        Permissions for hit.

    invalidate : Signal, in
        Invalidate the cache entry.

    invalidated : Signal, out
        Pulses when cache was invalidated.
    """

    def __init__(self):
        # =====================================================================
        # Cache Entry Storage
        # =====================================================================

        self.valid = Signal()

        # Input address range
        self._input_addr     = Signal(64)
        self._range_size     = Signal(32)
        self._input_addr_end = Signal(64)  # Precomputed: input_addr + range_size - 1

        # Output (translated) address
        self._output_addr = Signal(64)

        # Permissions
        self._permissions = Signal(8)

        # PASID context
        self._pasid_valid = Signal()
        self._pasid_val   = Signal(20)

        # =====================================================================
        # Store Interface
        # =====================================================================

        self.store             = Signal()
        self.store_input_addr  = Signal(64)
        self.store_output_addr = Signal(64)
        self.store_range_size  = Signal(32)
        self.store_permissions = Signal(8)
        self.store_pasid_valid = Signal()
        self.store_pasid_val   = Signal(20)

        # =====================================================================
        # Lookup Interface
        # =====================================================================

        self.lookup_addr       = Signal(64)
        self.lookup_pasid_valid = Signal()
        self.lookup_pasid_val  = Signal(20)
        self.lookup_hit        = Signal()
        self.lookup_output     = Signal(64)
        self.lookup_perm       = Signal(8)

        # =====================================================================
        # Invalidation Interface
        # =====================================================================

        self.invalidate  = Signal()
        self.invalidated = Signal()

        # # #

        # =====================================================================
        # Lookup Logic (Pipelined)
        # =====================================================================
        # Lookup is pipelined: inputs on cycle N, outputs valid on cycle N+1.
        # This breaks the long combinatorial path through 64-bit comparisons
        # and arithmetic. The DMA engine's SETUP state provides the required
        # cycle of latency.

        # Check if lookup address is in range
        # Uses precomputed _input_addr_end (computed at store time)
        addr_in_range = Signal()
        self.comb += addr_in_range.eq(
            (self.lookup_addr >= self._input_addr) &
            (self.lookup_addr <= self._input_addr_end)
        )

        # Check PASID match
        pasid_match = Signal()
        self.comb += pasid_match.eq(
            # Either both have no PASID, or PASID values match
            (~self._pasid_valid & ~self.lookup_pasid_valid) |
            (self._pasid_valid & self.lookup_pasid_valid &
             (self._pasid_val == self.lookup_pasid_val))
        )

        # Combinatorial hit (before pipeline register)
        atc_hit = Signal()
        self.comb += atc_hit.eq(self.valid & addr_in_range & pasid_match)

        # Calculate translated address (combinatorial)
        # Output = output_addr + (lookup_addr - input_addr)
        offset = Signal(64)
        lookup_output = Signal(64)
        self.comb += [
            offset.eq(self.lookup_addr - self._input_addr),
            lookup_output.eq(self._output_addr + offset),
        ]

        # Pipeline register for lookup outputs
        # This breaks the critical timing path
        self.sync += [
            self.lookup_hit.eq(atc_hit),
            self.lookup_output.eq(lookup_output),
            self.lookup_perm.eq(self._permissions),
        ]

        # =====================================================================
        # Store Logic
        # =====================================================================

        self.sync += [
            If(self.invalidate,
                self.valid.eq(0),
                self.invalidated.eq(1),
            ).Elif(self.store,
                self.valid.eq(1),
                self._input_addr.eq(self.store_input_addr),
                self._output_addr.eq(self.store_output_addr),
                self._range_size.eq(self.store_range_size),
                self._permissions.eq(self.store_permissions),
                self._pasid_valid.eq(self.store_pasid_valid),
                self._pasid_val.eq(self.store_pasid_val),
                self.invalidated.eq(0),
                # Precompute end address at store time to avoid combinational path
                self._input_addr_end.eq(self.store_input_addr + self.store_range_size - 1),
            ).Else(
                self.invalidated.eq(0),
            ),
        ]
