#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPONENT_DIR=$(cd "${SCRIPT_DIR}/.." && pwd)
REPO_ROOT=$(cd "${COMPONENT_DIR}/.." && pwd)
INSTALLER_DIR="${REPO_ROOT}/osac-installer"

if [[ "${OSAC_CSI_DEBUG_IT_APPROVED:-}" != "true" ]]; then
	echo "ERROR: set OSAC_CSI_DEBUG_IT_APPROVED=true only for a disposable Kind cluster." >&2
	exit 2
fi
if [[ -z "${KUBECONFIG:-}" ]]; then
	echo "ERROR: KUBECONFIG must explicitly select the disposable Kind cluster." >&2
	exit 2
fi

KIND_CLUSTER_NAME=${KIND_CLUSTER_NAME:-osac-dev}
context=$(kubectl config current-context)
if [[ "${context}" != "kind-${KIND_CLUSTER_NAME}" ]]; then
	echo "ERROR: expected Kind context kind-${KIND_CLUSTER_NAME}; found ${context}." >&2
	exit 2
fi

if helm status csi-driver --namespace osac-csi >/dev/null 2>&1; then
	echo "ERROR: CSI release csi-driver already exists; refusing to modify it." >&2
	exit 1
fi
if kubectl get namespace osac-csi >/dev/null 2>&1; then
	echo "ERROR: namespace osac-csi already exists; refusing to modify it." >&2
	exit 1
fi
if kubectl get csidriver csi.osac.openshift.io >/dev/null 2>&1; then
	echo "ERROR: CSIDriver csi.osac.openshift.io already exists; refusing to modify it." >&2
	exit 1
fi

namespace_created=false
cleanup() {
	local result=$?
	trap - EXIT
	set +e
	if helm status csi-driver --namespace osac-csi >/dev/null 2>&1; then
		helm uninstall csi-driver --namespace osac-csi --wait --timeout 5m >/dev/null 2>&1
	fi
	if [[ "${namespace_created}" == "true" ]]; then
		kubectl delete namespace osac-csi --wait=true --timeout=5m >/dev/null 2>&1
	fi
	exit "${result}"
}
trap cleanup EXIT

kubectl create namespace osac-csi
namespace_created=true

make_args=(
	-C "${INSTALLER_DIR}" install-csi-driver
	PLATFORM=kind PROFILE=dev NS=osac
	KIND_CLUSTER_NAME="${KIND_CLUSTER_NAME}" KUBECONFIG="${KUBECONFIG}"
	CSI_DRIVER_RELEASE=csi-driver CSI_DRIVER_NAMESPACE=osac-csi
	DEBUG=true CONTINUE=false CSI_DRIVER_DEBUG_CONTINUE=false
	CSI_DRIVER_NODE_DEBUG_CONTINUE=false BUILD=true PUSH=false
)
if [[ -n "${IMAGE_PLATFORM:-}" ]]; then
	make_args+=("IMAGE_PLATFORM=${IMAGE_PLATFORM}")
fi
make "${make_args[@]}"

kubectl rollout status deployment/csi-driver-controller \
	--namespace osac-csi --timeout=5m
kubectl rollout status daemonset/csi-driver-node \
	--namespace osac-csi --timeout=5m

controller_pod=$(kubectl get pods --namespace osac-csi \
	-l app.kubernetes.io/name=csi-driver,app.kubernetes.io/component=controller \
	-o jsonpath='{.items[0].metadata.name}')
if [[ -z "${controller_pod}" ]]; then
	echo "ERROR: CSI controller Pod was not created." >&2
	exit 1
fi

attach_helper="${REPO_ROOT}/tools/test-debug-attach.py"
for attempt in 1 2; do
	python3 "${attach_helper}" --namespace osac-csi --pod "${controller_pod}"
done

node_pods=$(kubectl get pods --namespace osac-csi \
	-l app.kubernetes.io/name=csi-driver,app.kubernetes.io/component=node \
	-o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
if [[ -z "${node_pods}" ]]; then
	echo "ERROR: CSI node DaemonSet did not create any Pods." >&2
	exit 1
fi
while IFS= read -r pod; do
	[[ -n "${pod}" ]] || continue
	for attempt in 1 2; do
		python3 "${attach_helper}" --namespace osac-csi --pod "${pod}"
	done
done <<< "${node_pods}"

echo "CSI controller and node Delve attach/re-attach checks passed."
