# Focused operator debugging is kept outside the main installer Makefile. It
# updates the existing umbrella release rather than adding a Deployment.
# Developer-only operator debug values use patternProperties so Helm validates
# this focused upgrade without adding Wizard controls.
ALL_TARGETS += install-osac-operator

REMOTE_DEBUG ?= false
DEBUG_CONTINUE ?= true
REMOTE_DEBUG_IMAGE ?= $(if $(filter kind,$(PLATFORM)),localhost/osac-operator:latest-debug,)
OPERATOR_VALUES_FILE ?=
FULFILLMENT_DOMAIN ?= $(shell oc get ingresses.config/cluster -o jsonpath='{.spec.domain}' 2>/dev/null)
DEBUG_SCC_RELEASE := osac-debug-scc
DEBUG_SCC_RELEASE_NAMESPACE := kube-system
DEBUG_SCC_CHART := charts/osac-debug-scc
DEBUG_SCC_NAME := osac-remote-debug
DEBUG_SCC_LEGACY_NAME := osac-operator-debug

# The main installer defaults KUBECONFIG to its profile-specific Kind cluster.
# These OpenShift-only targets have no PROFILE, so use the caller's kubeconfig
# or the conventional default instead.
enable-debug disable-debug: export KUBECONFIG := $(or $(KUBECONFIG),$(HOME)/.kube/config)

.PHONY: install-osac-operator
install-osac-operator: ## Update the operator in the existing OSAC release; REMOTE_DEBUG=true builds the debug image.
	@case "$(REMOTE_DEBUG)" in true|false) ;; \
		*) echo "ERROR: REMOTE_DEBUG must be true or false" >&2; exit 1 ;; \
	esac
	@case "$(DEBUG_CONTINUE)" in true|false) ;; \
		*) echo "ERROR: DEBUG_CONTINUE must be true or false" >&2; exit 1 ;; \
	esac
	@helm status osac --namespace "$(NS)" >/dev/null 2>&1 || { \
		echo "ERROR: Helm release 'osac' was not found in namespace '$(NS)'. Install OSAC first." >&2; \
		exit 1; \
	}
ifeq ($(PLATFORM),openshift)
	@if [ -z "$(FULFILLMENT_DOMAIN)" ]; then \
		echo "ERROR: Could not determine cluster domain. Is oc logged in? Set FULFILLMENT_DOMAIN explicitly if needed." >&2; \
		exit 1; \
	fi
endif
ifeq ($(and $(filter openshift,$(PLATFORM)),$(filter true,$(REMOTE_DEBUG))),true)
	@helm status "$(DEBUG_SCC_RELEASE)" --namespace "$(DEBUG_SCC_RELEASE_NAMESPACE)" >/dev/null 2>&1 || { \
		echo "ERROR: remote-debug SCC is not enabled; run 'make enable-debug' first." >&2; \
		exit 1; \
	}
	@kubectl get scc "$(DEBUG_SCC_NAME)" >/dev/null 2>&1 || { \
		echo "ERROR: Helm release exists but SCC '$(DEBUG_SCC_NAME)' is missing; run 'make enable-debug'." >&2; \
		exit 1; \
	}
endif
	rm -rf $(OSAC_CHART)/charts
	helm --repository-config $(LOCAL_HELM_REPOSITORY_CONFIG) dependency build $(OSAC_CHART)
	@if [ "$(REMOTE_DEBUG)" = true ] && [ -z "$(REMOTE_DEBUG_IMAGE)" ]; then \
		echo "ERROR: REMOTE_DEBUG_IMAGE is required when REMOTE_DEBUG=true" >&2; exit 1; \
	fi
	@if [ "$(REMOTE_DEBUG)" = true ]; then \
		$(MAKE) -C ../osac-operator image-build-debug \
			DEBUG_IMG="$(REMOTE_DEBUG_IMAGE)" \
			CONTAINER_TOOL="$(CONTAINER_TOOL)" \
			$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)"); \
	fi
ifeq ($(PLATFORM),kind)
	$(if $(filter true,$(REMOTE_DEBUG)),$(call kind-load-image,$(REMOTE_DEBUG_IMAGE)),@:)
else
	@if [ "$(REMOTE_DEBUG)" = true ]; then $(CONTAINER_TOOL) push "$(REMOTE_DEBUG_IMAGE)"; fi
endif
	@helm upgrade osac $(OSAC_CHART) \
		-f $(INSTANCE_VALUES) $(INSTANCE_VALUES_EXTRA) \
		$(if $(strip $(OPERATOR_VALUES_FILE)),-f "$(OPERATOR_VALUES_FILE)") \
		--namespace "$(NS)" \
		--no-hooks \
		--set-string global.osacDeploymentId=$(NS)/osac \
		$(if $(filter openshift,$(PLATFORM)),--set service.externalHostname=fulfillment-api-$(NS).$(FULFILLMENT_DOMAIN) --set service.internalHostname=fulfillment-internal-api-$(NS).$(FULFILLMENT_DOMAIN) --set global.clusterDomain=$(FULFILLMENT_DOMAIN) --set ui.auth.oidcIssuerURL=https://keycloak-keycloak.$(FULFILLMENT_DOMAIN)/realms/osac) \
		$(INSTANCE_HELM_ARGS) \
		$(EXTRA_HELM_ARGS) \
		--set operator.debug.enabled=$(REMOTE_DEBUG) \
		--set operator.debug.continue=$(DEBUG_CONTINUE) \
		--set operator.debug.fsGroup=$(if $(filter kind,$(PLATFORM)),1001,auto) \
		--set operator.debug.imagePullPolicy=$(if $(filter kind,$(PLATFORM)),IfNotPresent,Always) \
		$(if $(filter true,$(REMOTE_DEBUG)),--set-string operator.debug.image=$(REMOTE_DEBUG_IMAGE)) \
		$(OSAC_HELM_FORCE_CONFLICTS) \
		--wait --timeout 40m

.PHONY: enable-debug
enable-debug: ## Install the OpenShift remote-debug SCC through its own Helm release.
	@kubectl get --raw /apis/security.openshift.io/v1 >/dev/null 2>&1 || { \
		echo "ERROR: enable-debug requires an OpenShift cluster with the SCC API." >&2; \
		exit 1; \
	}
	@helm upgrade --install "$(DEBUG_SCC_RELEASE)" "$(DEBUG_SCC_CHART)" \
		--namespace "$(DEBUG_SCC_RELEASE_NAMESPACE)" \
		--wait --timeout 5m

.PHONY: disable-debug
disable-debug: ## Remove the OpenShift remote-debug SCC after no pods use it.
	@kubectl get --raw /apis/security.openshift.io/v1 >/dev/null 2>&1 || { \
		echo "ERROR: disable-debug requires an OpenShift cluster with the SCC API." >&2; \
		exit 1; \
	}
	@pods_using_scc="$$(kubectl get pods --all-namespaces \
		--field-selector=status.phase!=Succeeded,status.phase!=Failed \
		-o jsonpath='{range .items[*]}{.metadata.namespace}{" "}{.metadata.name}{" "}{.metadata.annotations.openshift\.io/scc}{"\n"}{end}' \
		| awk '$$3 == "$(DEBUG_SCC_NAME)" || $$3 == "$(DEBUG_SCC_LEGACY_NAME)" { print $$1 "/" $$2 }')"; \
	if [ -n "$$pods_using_scc" ]; then \
		echo "ERROR: cannot disable-debug; pods still use a managed or legacy debug SCC:" >&2; \
		printf '%s\n' "$$pods_using_scc" >&2; \
		exit 1; \
	fi; \
	release="$$(helm list --all --namespace "$(DEBUG_SCC_RELEASE_NAMESPACE)" \
		--filter '^$(DEBUG_SCC_RELEASE)$$' --short)"; \
	if [ -z "$$release" ]; then \
		echo "ERROR: Helm release '$(DEBUG_SCC_RELEASE)' was not found; refusing to remove an unmanaged SCC." >&2; \
		exit 1; \
	fi; \
	helm uninstall "$(DEBUG_SCC_RELEASE)" \
		--namespace "$(DEBUG_SCC_RELEASE_NAMESPACE)" \
		--wait --timeout 5m
	@if kubectl get scc "$(DEBUG_SCC_LEGACY_NAME)" >/dev/null 2>&1; then \
		echo "WARNING: unmanaged legacy SCC '$(DEBUG_SCC_LEGACY_NAME)' remains; review and remove it separately if it belongs to this debug setup." >&2; \
	fi

.PHONY: remote-debug-make-test
remote-debug-make-test: ## Verify the focused remote-debug Make workflows without a cluster.
	python3 scripts/test-remote-debug-make.py

.PHONY: remote-debug-render-test
remote-debug-render-test: ## Verify umbrella debug rendering without exposing Wizard schema fields.
	rm -rf $(OSAC_CHART)/charts
	helm --repository-config $(LOCAL_HELM_REPOSITORY_CONFIG) dependency build $(OSAC_CHART)
	python3 scripts/test-remote-debug-render.py
