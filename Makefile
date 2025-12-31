SHELL = /bin/bash

MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BSA_EXERCISER := bsa-pcie-exerciser

# Default platform (spec_a7 or squirrel)
PLATFORM ?= squirrel

.PHONY: build

help: ## Show this help
	@echo ""
	@echo "BSA PCIe Exerciser Makefile"
	@echo "==========================="
	@echo ""
	@echo "TARGETS:"
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-16s: %s\n", $$1, $$2}'
	@echo ""
	@echo "VARIABLES:"
	@echo "  PLATFORM        : Target platform (default: spec_a7)"
	@echo "                    Options: spec_a7, squirrel"

logs:
	@mkdir -p logs

build: logs ## Build the LiteX top level
	$(BSA_EXERCISER) build -p $(PLATFORM) |& tee logs/build.log

repopack: ## Package repo for upload to LLM
	repopack -i .venv,.git,build,build_*,logs,docs,external,.vscode,*.txt -o $(BSA_EXERCISER).txt

wc: ## Count non-empty, non-comment lines of Migen code
	@find src -name "*.py" -exec cat {} + | grep -vE '^\s*(#|$$)' | wc -l
