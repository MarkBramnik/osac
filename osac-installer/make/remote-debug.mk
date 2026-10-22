# Build/push images independently or with install-services; one focused target
# upgrades the shared OSAC release.
ALL_TARGETS += install-services

DEBUG ?= false
BUILD ?= true
PUSH ?= true
CONTINUE ?= true
DEBUG_CONTINUE ?= $(CONTINUE)
FULFILLMENT_DEBUG_CONTINUE ?= $(CONTINUE)
SERVICES ?=
OSAC_OPERATOR_IMAGE ?=
FULFILLMENT_SERVICE_IMAGE ?=
IMAGE_CONFIG ?= $(HOME)/.config/osac/image-mappings.json
OPERATOR_VALUES_FILE ?=
FULFILLMENT_VALUES_FILE ?=
FULFILLMENT_DOMAIN ?= $(shell oc get ingresses.config/cluster -o jsonpath='{.spec.domain}' 2>/dev/null)

# Make exports command-line variables automatically; explicitly export the
# makefile defaults too so the resolver sees the same values as this target.
export BUILD DEBUG FULFILLMENT_SERVICE_IMAGE IMAGE_CONFIG OSAC_OPERATOR_IMAGE PLATFORM PUSH SERVICES

comma := ,
empty :=
space := $(empty) $(empty)
SERVICE_NAMES := $(strip $(subst $(comma),$(space),$(SERVICES)))

# Resolve config/CLI image references at parse time so the same values can be
# passed to selected build goals, Kind image loading, and the Helm upgrade.
ifneq ($(filter install-services,$(MAKECMDGOALS)),)
shell_quote = '$(subst ','"'"',$(1))'
# A fresh pod-template annotation forces selected Deployments to recreate pods
# even when the image reference has not changed.
INSTALL_ROLLOUT_TOKEN := $(shell python3 -c 'from datetime import datetime, timezone; print(datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"))')
REMOTE_DEBUG_IMAGE_FIELDS := $(shell IMAGE_CONFIG=$(call shell_quote,$(IMAGE_CONFIG)) python3 scripts/remote_debug_image_config.py --make-vars)
CONFIG_NORMAL_OPERATOR_IMAGE := $(patsubst normal-operator=%,%,$(filter normal-operator=%,$(REMOTE_DEBUG_IMAGE_FIELDS)))
CONFIG_DEBUG_OPERATOR_IMAGE := $(patsubst debug-operator=%,%,$(filter debug-operator=%,$(REMOTE_DEBUG_IMAGE_FIELDS)))
CONFIG_NORMAL_FULFILLMENT_IMAGE := $(patsubst normal-fulfillment=%,%,$(filter normal-fulfillment=%,$(REMOTE_DEBUG_IMAGE_FIELDS)))
CONFIG_DEBUG_FULFILLMENT_IMAGE := $(patsubst debug-fulfillment=%,%,$(filter debug-fulfillment=%,$(REMOTE_DEBUG_IMAGE_FIELDS)))
RESOLVED_OSAC_OPERATOR_IMAGE := $(if $(strip $(OSAC_OPERATOR_IMAGE)),$(OSAC_OPERATOR_IMAGE),$(if $(filter true,$(DEBUG)),$(CONFIG_DEBUG_OPERATOR_IMAGE),$(CONFIG_NORMAL_OPERATOR_IMAGE)))
RESOLVED_FULFILLMENT_SERVICE_IMAGE := $(if $(strip $(FULFILLMENT_SERVICE_IMAGE)),$(FULFILLMENT_SERVICE_IMAGE),$(if $(filter true,$(DEBUG)),$(CONFIG_DEBUG_FULFILLMENT_IMAGE),$(CONFIG_NORMAL_FULFILLMENT_IMAGE)))
ifeq ($(RESOLVED_OSAC_OPERATOR_IMAGE),-)
RESOLVED_OSAC_OPERATOR_IMAGE :=
endif
ifeq ($(RESOLVED_FULFILLMENT_SERVICE_IMAGE),-)
RESOLVED_FULFILLMENT_SERVICE_IMAGE :=
endif
endif

OSAC_OPERATOR_LOCAL_IMAGE = $(if $(filter true,$(DEBUG)),localhost/osac-operator:latest-debug,localhost/osac-operator:latest)
FULFILLMENT_SERVICE_LOCAL_IMAGE = $(if $(filter true,$(DEBUG)),localhost/fulfillment-service:latest-debug,localhost/fulfillment-service:latest)
OSAC_OPERATOR_IMAGE_EFFECTIVE = $(if $(strip $(OSAC_OPERATOR_IMAGE)),$(OSAC_OPERATOR_IMAGE),$(OSAC_OPERATOR_LOCAL_IMAGE))
FULFILLMENT_SERVICE_IMAGE_EFFECTIVE = $(if $(strip $(FULFILLMENT_SERVICE_IMAGE)),$(FULFILLMENT_SERVICE_IMAGE),$(FULFILLMENT_SERVICE_LOCAL_IMAGE))
OSAC_OPERATOR_INSTALL_IMAGE = $(if $(strip $(RESOLVED_OSAC_OPERATOR_IMAGE)),$(RESOLVED_OSAC_OPERATOR_IMAGE),$(if $(and $(filter kind,$(PLATFORM)),$(or $(filter true,$(DEBUG)),$(and $(filter true,$(BUILD)),$(filter false,$(PUSH))))),$(OSAC_OPERATOR_LOCAL_IMAGE)))
FULFILLMENT_SERVICE_INSTALL_IMAGE = $(if $(strip $(RESOLVED_FULFILLMENT_SERVICE_IMAGE)),$(RESOLVED_FULFILLMENT_SERVICE_IMAGE),$(if $(and $(filter kind,$(PLATFORM)),$(or $(filter true,$(DEBUG)),$(and $(filter true,$(BUILD)),$(filter false,$(PUSH))))),$(FULFILLMENT_SERVICE_LOCAL_IMAGE)))

DEBUG_SCC_RELEASE := osac-debug-scc
DEBUG_SCC_RELEASE_NAMESPACE := kube-system
DEBUG_SCC_CHART := charts/osac-debug-scc
DEBUG_SCC_NAME := osac-remote-debug
DEBUG_SCC_LEGACY_NAME := osac-operator-debug
DEBUG_SCC_TARGET_NAMESPACE ?= $(if $(strip $(NS)),$(NS),osac)

# The main installer defaults KUBECONFIG to its profile-specific Kind cluster.
# SCC-only targets have no PROFILE, so use the caller's kubeconfig or the default.
enable-debug disable-debug: export KUBECONFIG := $(or $(KUBECONFIG),$(HOME)/.kube/config)

.PHONY: build-osac-operator
build-osac-operator: ## Build (and by default push) the normal or Delve-enabled operator image.
	@case "$(DEBUG)" in true|false) ;; \
		*) echo "ERROR: DEBUG must be true or false" >&2; exit 1 ;; \
	esac
	@case "$(PUSH)" in true|false) ;; \
		*) echo "ERROR: PUSH must be true or false" >&2; exit 1 ;; \
	esac
	@if [ "$(PUSH)" = true ] && [ -z "$(OSAC_OPERATOR_IMAGE)" ]; then \
		echo "ERROR: OSAC_OPERATOR_IMAGE is required when PUSH=true" >&2; exit 1; \
	fi
ifeq ($(DEBUG),true)
	$(MAKE) -C ../osac-operator image-build-debug \
		DEBUG_IMG="$(OSAC_OPERATOR_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)" \
		$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)")
ifeq ($(PUSH),true)
	$(MAKE) -C ../osac-operator image-push-debug \
		DEBUG_IMG="$(OSAC_OPERATOR_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)"
endif
else
	$(MAKE) -C ../osac-operator image-build \
		IMG="$(OSAC_OPERATOR_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)" \
		$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)")
ifeq ($(PUSH),true)
	$(MAKE) -C ../osac-operator image-push \
		IMG="$(OSAC_OPERATOR_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)"
endif
endif

.PHONY: build-fulfillment-service
build-fulfillment-service: ## Build (and by default push) the normal or Delve-enabled Fulfillment image.
	@case "$(DEBUG)" in true|false) ;; \
		*) echo "ERROR: DEBUG must be true or false" >&2; exit 1 ;; \
	esac
	@case "$(PUSH)" in true|false) ;; \
		*) echo "ERROR: PUSH must be true or false" >&2; exit 1 ;; \
	esac
	@if [ "$(PUSH)" = true ] && [ -z "$(FULFILLMENT_SERVICE_IMAGE)" ]; then \
		echo "ERROR: FULFILLMENT_SERVICE_IMAGE is required when PUSH=true" >&2; exit 1; \
	fi
ifeq ($(DEBUG),true)
	$(MAKE) -C ../fulfillment-service image-build-debug \
		DEBUG_IMG="$(FULFILLMENT_SERVICE_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)" \
		$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)")
ifeq ($(PUSH),true)
	$(MAKE) -C ../fulfillment-service image-push-debug \
		DEBUG_IMG="$(FULFILLMENT_SERVICE_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)"
endif
else
	$(MAKE) -C ../fulfillment-service image-build \
		IMG="$(FULFILLMENT_SERVICE_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)" \
		$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)")
ifeq ($(PUSH),true)
	$(MAKE) -C ../fulfillment-service image-push \
		IMG="$(FULFILLMENT_SERVICE_IMAGE_EFFECTIVE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)"
endif
endif

.PHONY: install-services
install-services: ## Build selected images by default, force their rollout, then upgrade OSAC once.
	@case "$(BUILD)" in true|false) ;; \
		*) echo "ERROR: BUILD must be true or false" >&2; exit 1 ;; \
	esac
	@case "$(DEBUG)" in true|false) ;; \
		*) echo "ERROR: DEBUG must be true or false" >&2; exit 1 ;; \
	esac
	@case "$(CONTINUE)" in ''|true|false) ;; \
		*) echo "ERROR: CONTINUE must be true or false when set" >&2; exit 1 ;; \
	esac
	@case "$(DEBUG_CONTINUE)" in true|false) ;; \
		*) echo "ERROR: DEBUG_CONTINUE must be true or false" >&2; exit 1 ;; \
	esac
	@case "$(FULFILLMENT_DEBUG_CONTINUE)" in true|false) ;; \
		*) echo "ERROR: FULFILLMENT_DEBUG_CONTINUE must be true or false" >&2; exit 1 ;; \
	esac
	@python3 scripts/remote_debug_image_config.py --validate-only
	@python3 scripts/remote_debug_helm_args.py \
		--services "$(SERVICES)" --debug "$(DEBUG)" \
		--platform "$(PLATFORM)" --debug-continue "$(DEBUG_CONTINUE)" \
		--fulfillment-debug-continue "$(FULFILLMENT_DEBUG_CONTINUE)" \
		--rollout-token "$(INSTALL_ROLLOUT_TOKEN)" \
		--osac-operator-image "$(OSAC_OPERATOR_INSTALL_IMAGE)" \
		--fulfillment-service-image "$(FULFILLMENT_SERVICE_INSTALL_IMAGE)" \
		--validate-only
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
ifeq ($(and $(PLATFORM),$(filter openshift,$(PLATFORM)),$(filter true,$(DEBUG))),true)
	@helm status "$(DEBUG_SCC_RELEASE)" --namespace "$(DEBUG_SCC_RELEASE_NAMESPACE)" >/dev/null 2>&1 || { \
		echo "ERROR: remote-debug SCC is not enabled; run 'make enable-debug NS=$(NS)' first." >&2; \
		exit 1; \
	}
	@kubectl get scc "$(DEBUG_SCC_NAME)" >/dev/null 2>&1 || { \
		echo "ERROR: Helm release exists but SCC '$(DEBUG_SCC_NAME)' is missing; run 'make enable-debug NS=$(NS)'." >&2; \
		exit 1; \
	}
	@authorized_user="system:serviceaccount:$(NS):debug"; \
		scc_users="$$(kubectl get scc "$(DEBUG_SCC_NAME)" -o jsonpath='{.users[*]}')"; \
		case " $$scc_users " in *" $$authorized_user "*) ;; *) \
			echo "ERROR: SCC '$(DEBUG_SCC_NAME)' is not scoped to $$authorized_user; run 'make enable-debug NS=$(NS)' first." >&2; \
			exit 1 ;; esac; \
		scc_groups="$$(kubectl get scc "$(DEBUG_SCC_NAME)" -o jsonpath='{.groups[*]}')"; \
		if [ -n "$$scc_groups" ]; then \
			echo "ERROR: SCC '$(DEBUG_SCC_NAME)' has an unexpected group grant; reinstall it with 'make enable-debug NS=$(NS)'." >&2; \
			exit 1; \
		fi
endif
	@rm -rf $(OSAC_CHART)/charts
	@helm --repository-config $(LOCAL_HELM_REPOSITORY_CONFIG) dependency build $(OSAC_CHART)
ifeq ($(BUILD),true)
ifneq ($(filter osac-operator,$(SERVICE_NAMES)),)
	$(MAKE) --no-print-directory build-osac-operator \
		DEBUG="$(DEBUG)" PUSH="$(PUSH)" \
		OSAC_OPERATOR_IMAGE="$(RESOLVED_OSAC_OPERATOR_IMAGE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)" \
		$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)")
endif
ifneq ($(filter fulfillment-service,$(SERVICE_NAMES)),)
	$(MAKE) --no-print-directory build-fulfillment-service \
		DEBUG="$(DEBUG)" PUSH="$(PUSH)" \
		FULFILLMENT_SERVICE_IMAGE="$(RESOLVED_FULFILLMENT_SERVICE_IMAGE)" \
		CONTAINER_TOOL="$(CONTAINER_TOOL)" \
		$(if $(strip $(IMAGE_PLATFORM)),IMAGE_PLATFORM="$(IMAGE_PLATFORM)")
endif
endif
ifeq ($(PLATFORM),kind)
ifneq ($(filter osac-operator,$(SERVICE_NAMES)),)
	$(if $(strip $(OSAC_OPERATOR_INSTALL_IMAGE)),$(call kind-load-image,$(OSAC_OPERATOR_INSTALL_IMAGE)))
endif
ifneq ($(filter fulfillment-service,$(SERVICE_NAMES)),)
	$(if $(strip $(FULFILLMENT_SERVICE_INSTALL_IMAGE)),$(call kind-load-image,$(FULFILLMENT_SERVICE_INSTALL_IMAGE)))
endif
endif
	@helm_helm_args="$(shell python3 scripts/remote_debug_helm_args.py \
		--services '$(SERVICES)' --debug '$(DEBUG)' \
		--platform '$(PLATFORM)' --debug-continue '$(DEBUG_CONTINUE)' \
		--fulfillment-debug-continue '$(FULFILLMENT_DEBUG_CONTINUE)' \
		--rollout-token '$(INSTALL_ROLLOUT_TOKEN)' \
		--osac-operator-image '$(OSAC_OPERATOR_INSTALL_IMAGE)' \
		--fulfillment-service-image '$(FULFILLMENT_SERVICE_INSTALL_IMAGE)' \
		--helm-args)"; \
		helm upgrade osac $(OSAC_CHART) \
			-f $(INSTANCE_VALUES) $(INSTANCE_VALUES_EXTRA) \
			$(if $(strip $(OPERATOR_VALUES_FILE)),-f "$(OPERATOR_VALUES_FILE)") \
			$(if $(strip $(FULFILLMENT_VALUES_FILE)),-f "$(FULFILLMENT_VALUES_FILE)") \
			--namespace "$(NS)" --create-namespace \
			--no-hooks \
			--set-string global.osacDeploymentId=$(NS)/osac \
			$(if $(filter openshift,$(PLATFORM)),--set service.externalHostname=fulfillment-api-$(NS).$(FULFILLMENT_DOMAIN) --set service.internalHostname=fulfillment-internal-api-$(NS).$(FULFILLMENT_DOMAIN) --set global.clusterDomain=$(FULFILLMENT_DOMAIN) --set ui.auth.oidcIssuerURL=https://keycloak-keycloak.$(FULFILLMENT_DOMAIN)/realms/osac) \
			$(INSTANCE_HELM_ARGS) \
			$(EXTRA_HELM_ARGS) \
			$$helm_helm_args \
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
		--set-string targetNamespace="$(DEBUG_SCC_TARGET_NAMESPACE)" \
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

.PHONY: remote-debug-scc-integration-test
remote-debug-scc-integration-test: ## Test SCC admission and removal on an explicitly approved OpenShift cluster.
	./scripts/test-remote-debug-scc-openshift.sh
