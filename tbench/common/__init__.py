#
# BSA PCIe Exerciser - Common Test Infrastructure
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#

"""
Common test infrastructure for BSA PCIe Exerciser integration tests.

This package provides:
- TestPlatform: Minimal LiteX platform stub for simulation
- PHYStub: Drop-in replacement for S7PCIEPHY without Xilinx primitives
- PCIeBFM: Bus Functional Model for PHY-level TLP injection/capture
- TLPBuilder: Helper functions for constructing TLP packets
"""

from tbench.common.platform import TestPlatform
from tbench.common.phy_stub import PHYStub
from tbench.common.pcie_bfm import PCIeBFM, TLPRequestSource, TLPCompletionSink
from tbench.common.tlp_builder import TLPBuilder

__all__ = [
    'TestPlatform',
    'PHYStub',
    'PCIeBFM',
    'TLPRequestSource',
    'TLPCompletionSink',
    'TLPBuilder',
]
