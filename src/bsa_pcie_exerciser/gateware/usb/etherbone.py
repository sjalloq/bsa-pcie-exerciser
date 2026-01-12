#
# USB Etherbone - Wishbone over USB using Etherbone Protocol
#
# Copyright (c) 2025-2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# This module re-exports from etherbone_adapter.py which uses LiteEth's
# battle-tested Etherbone components with a thin USB transport adapter.
#

from .etherbone_adapter import USBEtherbone, USBEtherbonePacketTX, USBEtherbonePacketRX

# Backward compatibility alias
Etherbone = USBEtherbone

__all__ = ['USBEtherbone', 'Etherbone', 'USBEtherbonePacketTX', 'USBEtherbonePacketRX']
