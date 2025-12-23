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

from tests.common.platform import TestPlatform
from tests.common.phy_stub import PHYStub
from tests.common.pcie_bfm import PCIeBFM, TLPRequestSource, TLPCompletionSink
from tests.common.tlp_builder import TLPBuilder

__all__ = [
    'TestPlatform',
    'PHYStub',
    'PCIeBFM',
    'TLPRequestSource',
    'TLPCompletionSink',
    'TLPBuilder',
]
