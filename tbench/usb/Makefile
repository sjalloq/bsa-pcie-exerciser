# USB Tests Cocotb Makefile
#
# Copyright (c) 2025 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause

SIM ?= verilator
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES = $(shell pwd)/build/sim/tb_usb.v
TOPLEVEL = tb_usb

# Test modules to run
COCOTB_TEST_MODULES ?= test_etherbone,test_stress,test_corner_cases,test_arbiter,test_cdc,test_monitor_rx,test_monitor_tx,test_golden_reference,test_requester_id,test_ats_invalidation,test_dma_ordering,test_pasid_switching,test_monitor_timing

# Verilator settings
ifeq ($(SIM),verilator)
    EXTRA_ARGS += --trace --trace-fst --trace-structs
    EXTRA_ARGS += -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-INITIALDLY -Wno-COMBDLY
    EXTRA_ARGS += -Wno-UNOPTFLAT -Wno-CASEINCOMPLETE -Wno-UNSIGNED -Wno-UNUSEDSIGNAL
    EXTRA_ARGS += -Wno-PINMISSING -Wno-TIMESCALEMOD
    COMPILE_ARGS += -CFLAGS "-std=c++17"
endif

include $(shell cocotb-config --makefiles)/Makefile.sim

# Generate Verilog from Migen testbench
$(VERILOG_SOURCES): tb_usb.py
	@echo "Generating Verilog from Migen..."
	@mkdir -p build/sim
	python tb_usb.py
	@cp -f build/sim/*.init . 2>/dev/null || true

# Convenience targets
.PHONY: gen
gen: $(VERILOG_SOURCES)

.PHONY: etherbone
etherbone:
	$(MAKE) sim MODULE=test_etherbone

.PHONY: monitor-rx
monitor-rx:
	$(MAKE) sim MODULE=test_monitor_rx

.PHONY: monitor-tx
monitor-tx:
	$(MAKE) sim MODULE=test_monitor_tx

.PHONY: cdc
cdc:
	$(MAKE) sim MODULE=test_cdc

.PHONY: arbiter
arbiter:
	$(MAKE) sim MODULE=test_arbiter

.PHONY: stress
stress:
	$(MAKE) sim MODULE=test_stress

.PHONY: corner-cases
corner-cases:
	$(MAKE) sim MODULE=test_corner_cases

.PHONY: golden
golden:
	$(MAKE) sim MODULE=test_golden_reference

.PHONY: requester-id
requester-id:
	$(MAKE) sim MODULE=test_requester_id

.PHONY: ats-invalidation
ats-invalidation:
	$(MAKE) sim MODULE=test_ats_invalidation

.PHONY: dma-ordering
dma-ordering:
	$(MAKE) sim MODULE=test_dma_ordering

.PHONY: pasid-switching
pasid-switching:
	$(MAKE) sim MODULE=test_pasid_switching

.PHONY: monitor-timing
monitor-timing:
	$(MAKE) sim MODULE=test_monitor_timing

.PHONY: all-tests
all-tests:
	$(MAKE) sim MODULE=test_etherbone,test_stress,test_corner_cases,test_arbiter,test_cdc,test_monitor_rx,test_monitor_tx,test_golden_reference,test_requester_id,test_ats_invalidation,test_dma_ordering,test_pasid_switching,test_monitor_timing

# Individual test targets
.PHONY: test-id
test-id:
	$(MAKE) sim MODULE=test_etherbone TESTCASE=test_etherbone_read_id_register

.PHONY: test-write-read
test-write-read:
	$(MAKE) sim MODULE=test_etherbone TESTCASE=test_etherbone_write_read

# Clean
.PHONY: clean
clean::
	rm -rf build/ sim_build/ __pycache__/ results.xml *.fst *.init dump.vcd

.PHONY: clean-all
clean-all: clean
	rm -rf .pytest_cache/ .coverage coverage.xml

# Help
.PHONY: help
help:
	@echo "USB Testbench Targets:"
	@echo "  gen             - Generate Verilog from Migen"
	@echo "  sim             - Run tests (set MODULE=test_xxx)"
	@echo "  etherbone       - Run Etherbone CSR tests"
	@echo "  stress          - Run stress tests (flood, backpressure, etc.)"
	@echo "  corner-cases    - Run corner case tests (header-only, single-beat, etc.)"
	@echo "  golden          - Run golden reference tests (comprehensive field verification)"
	@echo "  monitor-rx      - Run RX monitor tests"
	@echo "  monitor-tx      - Run TX monitor tests"
	@echo "  arbiter         - Run arbiter tests"
	@echo "  cdc             - Run CDC tests"
	@echo "  requester-id    - Run requester ID spoofing tests (BSA e001)"
	@echo "  ats-invalidation - Run ATS invalidation message tests (BSA e023-e025)"
	@echo "  dma-ordering    - Run multi-DMA ordering tests (BSA e026-e029)"
	@echo "  pasid-switching - Run PASID context switching tests (BSA e035-e036)"
	@echo "  monitor-timing  - Run monitor timing verification tests"
	@echo "  all-tests       - Run all test modules"
	@echo "  clean           - Clean build artifacts"
	@echo ""
	@echo "Examples:"
	@echo "  make golden"
	@echo "  make stress"
	@echo "  make requester-id"
	@echo "  make sim MODULE=test_golden_reference TESTCASE=test_golden_mrd32_all_fields"
