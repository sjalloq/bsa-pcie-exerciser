# Etherbone Standalone Tests Cocotb Makefile
#
# Copyright (c) 2026 Shareef Jalloq
# SPDX-License-Identifier: BSD-2-Clause

SIM ?= verilator
TOPLEVEL_LANG ?= verilog

VERILOG_SOURCES = $(shell pwd)/build/sim/tb_etherbone.v
TOPLEVEL = tb_etherbone

# Test modules to run
COCOTB_TEST_MODULES ?= test_basic

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
$(VERILOG_SOURCES): tb_etherbone.py
	@echo "Generating Verilog from Migen..."
	@mkdir -p build/sim
	python tb_etherbone.py
	@cp -f build/sim/*.init . 2>/dev/null || true

# Convenience targets
.PHONY: gen
gen: $(VERILOG_SOURCES)

.PHONY: basic
basic:
	$(MAKE) sim MODULE=test_basic

.PHONY: probe
probe:
	$(MAKE) sim MODULE=test_basic TESTCASE=test_probe_response

.PHONY: read
read:
	$(MAKE) sim MODULE=test_basic TESTCASE=test_single_read

.PHONY: write
write:
	$(MAKE) sim MODULE=test_basic TESTCASE=test_single_write

.PHONY: burst
burst:
	$(MAKE) sim MODULE=test_basic TESTCASE=test_burst_read

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
	@echo "Etherbone Standalone Testbench Targets:"
	@echo "  gen      - Generate Verilog from Migen"
	@echo "  sim      - Run tests (set MODULE=test_xxx)"
	@echo "  basic    - Run basic tests (probe, read, write)"
	@echo "  probe    - Run probe response test"
	@echo "  read     - Run single read test"
	@echo "  write    - Run single write test"
	@echo "  burst    - Run burst read test"
	@echo "  clean    - Clean build artifacts"
	@echo ""
	@echo "Examples:"
	@echo "  make basic"
	@echo "  make sim MODULE=test_basic TESTCASE=test_single_read"
