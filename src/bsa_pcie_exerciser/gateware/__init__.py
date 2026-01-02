#
# BSA PCIe Exerciser - Gateware Package
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause
#
# FPGA gateware modules for the BSA PCIe Exerciser.
#
# This package requires the [gateware] optional dependencies:
# - migen
# - litex
# - litepcie
#
# Install with: pip install bsa-pcie-exerciser[gateware]
#

# Note: We don't import submodules here to avoid pulling in gateware
# dependencies when only the tools are needed. Import explicitly:
#
#   from bsa_pcie_exerciser.gateware.soc.base import BSAExerciserSoC
#   from bsa_pcie_exerciser.gateware.core import BSARegisters
#
