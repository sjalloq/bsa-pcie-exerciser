SHELL = /bin/bash

MAKEFILE_DIR := $(dir $(abspath $(lastword $(MAKEFILE_LIST))))
BSA_EXERCISER := bsa-pcie-exerciser

# Default platform (spec_a7, squirrel, or captain)
PLATFORM ?= captain
PLATFORMS ?= spec_a7 squirrel captain

RELEASE_TAG ?= $(shell git describe --tags --dirty --always 2>/dev/null)
RELEASE_DIR ?= release/$(RELEASE_TAG)
RELEASE_NOTES ?= $(RELEASE_DIR)/RELEASE_NOTES.md
VIVADO_VERSION ?= unknown

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
	@echo "  PLATFORM        : Target platform (default: captain)"
	@echo "                    Options: spec_a7, squirrel, captain"

logs:
	@mkdir -p logs

build: logs ## Build the LiteX top level
	$(BSA_EXERCISER) build -p $(PLATFORM) |& tee logs/build.log

build-all: logs ## Build bitfiles for all platforms
	@for p in $(PLATFORMS); do \
		echo "== Building $$p =="; \
		$(BSA_EXERCISER) build -p $$p |& tee logs/build-$$p.log; \
	done

release-dir: ## Create release directory
	@mkdir -p $(RELEASE_DIR)

release-notes: release-dir ## Create a release notes stub if missing
	@if [ ! -f "$(RELEASE_NOTES)" ]; then \
		printf "# Release $(RELEASE_TAG)\n\n- Built from %s\n" "$$(git rev-parse HEAD)" > "$(RELEASE_NOTES)"; \
	fi

release-artifacts: release-dir ## Collect bitfiles for all platforms
	@for p in $(PLATFORMS); do \
		bit="build/$$p/gateware/$$p.bit"; \
		if [ ! -f "$$bit" ]; then \
			echo "Missing $$bit (run 'make build-all')"; \
			exit 1; \
		fi; \
		cp "$$bit" "$(RELEASE_DIR)/$(BSA_EXERCISER)_$(RELEASE_TAG)_$$p.bit"; \
	done

release-metadata: release-dir ## Write release metadata
	@{ \
		echo "release_tag: $(RELEASE_TAG)"; \
		echo "git_commit: $$(git rev-parse HEAD)"; \
		echo "git_describe: $$(git describe --tags --dirty --always 2>/dev/null)"; \
		echo "build_utc: $$(date -u +%Y-%m-%dT%H:%M:%SZ)"; \
		echo "platforms: $(PLATFORMS)"; \
		echo "vivado_version: $(VIVADO_VERSION)"; \
	} > "$(RELEASE_DIR)/BUILD_INFO.txt"

release-sums: release-dir ## Generate SHA256 sums for release artifacts
	@(cd $(RELEASE_DIR) && sha256sum * > SHA256SUMS.txt)

release: build-all release-artifacts release-metadata release-notes release-sums ## Build and stage a full release

release-upload: release ## Upload release artifacts with GitHub CLI
	@gh release create "$(RELEASE_TAG)" \
		--title "$(RELEASE_TAG)" \
		--notes-file "$(RELEASE_NOTES)" \
		"$(RELEASE_DIR)"/*

repopack: ## Package repo for upload to LLM
	repopack -i .venv,.git,build,build_*,logs,docs,external,.vscode,*.txt,software/target,cargo.lock -o $(BSA_EXERCISER).txt

wc: ## Count non-empty, non-comment lines of Migen code
	@find src -name "*.py" -exec cat {} + | grep -vE '^\s*(#|$$)' | wc -l
