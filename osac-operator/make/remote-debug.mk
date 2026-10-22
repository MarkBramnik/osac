# Remote-debug image targets are kept separate from the main Makefile so this
# fast-changing development workflow does not add churn to routine build rules.

DEBUG_HOST_ARCH := $(shell uname -m)
ifneq ($(filter arm64 aarch64,$(DEBUG_HOST_ARCH)),)
IMAGE_PLATFORM ?= linux/arm64
else ifneq ($(filter x86_64 amd64,$(DEBUG_HOST_ARCH)),)
IMAGE_PLATFORM ?= linux/amd64
else
IMAGE_PLATFORM ?= unsupported
endif
DEBUG_IMG ?= ghcr.io/osac-project/osac-operator:latest-debug

.PHONY: image-build-debug
image-build-debug: ## Build the Delve-enabled manager image for IMAGE_PLATFORM.
	@case "$(IMAGE_PLATFORM)" in \
		linux/amd64|linux/arm64) ;; \
		*) echo "ERROR: debug images support IMAGE_PLATFORM=linux/amd64 or linux/arm64 (got '$(IMAGE_PLATFORM)')" >&2; exit 1 ;; \
	esac
	$(CONTAINER_TOOL) build --platform=$(IMAGE_PLATFORM) --target debug \
		-t $(DEBUG_IMG) -f $(CONTAINERFILE) ..

.PHONY: image-push-debug
image-push-debug: ## Push the Delve-enabled manager image.
	$(CONTAINER_TOOL) push $(DEBUG_IMG)

.PHONY: test-debug-entrypoint
test-debug-entrypoint: ## Test Delve startup arguments for both continue modes.
	bash hack/test-debug-entrypoint.sh
