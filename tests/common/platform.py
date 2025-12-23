#
# BSA PCIe Exerciser - Test Platform Stub
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Minimal platform stub for simulation.

Provides just enough to satisfy LiteX SoCMini and component instantiation
without requiring real FPGA platform definitions or Xilinx primitives.
"""

from migen import Signal, Record


class TestPlatform:
    """
    Minimal platform stub for simulation.

    Provides just enough to satisfy SoCMini and component instantiation.
    """
    device = "xc7a35t"
    name = "test_platform"

    def __init__(self):
        self._signals = {}
        self._constraints = []
        self._sources = []

    def request(self, name, number=None, loose=False):
        """Return dummy signals for any pad request."""
        key = (name, number)
        if key not in self._signals:
            # Create appropriate dummy based on common names
            if "pcie" in name:
                self._signals[key] = self._make_pcie_pads()
            elif "clk" in name:
                self._signals[key] = Signal(name=f"{name}")
            else:
                self._signals[key] = Signal(name=f"{name}_{number}" if number else name)
        return self._signals[key]

    def _make_pcie_pads(self):
        """Create dummy PCIe pads record."""
        pads = Record([
            ("tx_p", 1), ("tx_n", 1),
            ("rx_p", 1), ("rx_n", 1),
            ("clk_p", 1), ("clk_n", 1),
            ("rst_n", 1),
        ])
        return pads

    def add_period_constraint(self, *args, **kwargs):
        pass

    def add_false_path_constraints(self, *args, **kwargs):
        pass

    def add_platform_command(self, *args, **kwargs):
        pass

    def add_extension(self, *args, **kwargs):
        pass

    def add_source(self, *args, **kwargs):
        pass

    def finalize(self, *args, **kwargs):
        pass

    @property
    def toolchain(self):
        return self

    @property
    def pre_synthesis_commands(self):
        return []

    def append(self, *args):
        """For toolchain.pre_synthesis_commands.append()"""
        pass
