#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
INSTALLER_DIR="$(cd -- "${SCRIPT_DIR}/.." >/dev/null && pwd)"
source "${SCRIPT_DIR}/lib.sh"

if [[ "${OSAC_DEBUG_SCC_IT_APPROVED:-}" != "true" ]]; then
  echo "ERROR: set OSAC_DEBUG_SCC_IT_APPROVED=true only for an approved disposable or coordinated OpenShift cluster." >&2
  exit 2
fi
if [[ -z "${KUBECONFIG:-}" ]]; then
  echo "ERROR: KUBECONFIG must explicitly select the OpenShift test cluster." >&2
  exit 2
fi

for command in kubectl helm oc; do
  command -v "${command}" >/dev/null || {
    echo "ERROR: required command '${command}' was not found." >&2
    exit 1
  }
done

kubectl get --raw /apis/security.openshift.io/v1 >/dev/null 2>&1 || {
  echo "ERROR: the selected cluster does not expose the OpenShift SCC API." >&2
  exit 1
}

existing_release="$(helm list --all --short --namespace kube-system --filter '^osac-debug-scc$')"
if [[ -n "${existing_release}" ]]; then
  echo "ERROR: Helm release 'osac-debug-scc' already exists; refusing to modify shared SCC state." >&2
  exit 1
fi
for scc in osac-remote-debug osac-operator-debug; do
  existing_scc="$(kubectl get scc "${scc}" --ignore-not-found -o name)"
  if [[ -n "${existing_scc}" ]]; then
    echo "ERROR: SCC '${scc}' already exists; refusing to modify shared SCC state." >&2
    exit 1
  fi
done

TEST_NAMESPACE="${OSAC_DEBUG_SCC_TEST_NAMESPACE:-osac-debug-scc-it-$(date +%s)-$$}"
TEST_POD="debug-scc-admission-test"
TEST_IMAGE="${OSAC_DEBUG_SCC_TEST_IMAGE:-registry.access.redhat.com/ubi10-minimal:1791444565}"
if [[ ! "${TEST_NAMESPACE}" =~ ^[a-z0-9]([-a-z0-9]*[a-z0-9])?$ || ${#TEST_NAMESPACE} -gt 63 ]]; then
  echo "ERROR: test namespace must be a Kubernetes DNS label of at most 63 characters." >&2
  exit 2
fi
if [[ ! "${TEST_IMAGE}" =~ ^[A-Za-z0-9][A-Za-z0-9._/:@-]*$ ]]; then
  echo "ERROR: OSAC_DEBUG_SCC_TEST_IMAGE must be a simple container image reference." >&2
  exit 2
fi

existing_namespace="$(kubectl get namespace "${TEST_NAMESPACE}" --ignore-not-found -o name)"
if [[ -n "${existing_namespace}" ]]; then
  echo "ERROR: test namespace '${TEST_NAMESPACE}' already exists; refusing to modify it." >&2
  exit 1
fi

namespace_created=false
scc_enable_attempted=false
cleanup() {
  local result=$?
  trap - EXIT
  set +e
	if [[ "${namespace_created}" == "true" ]]; then
		kubectl delete pod "${TEST_POD}" --namespace "${TEST_NAMESPACE}" \
			--ignore-not-found --wait=true --timeout=60s >/dev/null 2>&1
	fi
	if [[ "${scc_enable_attempted}" == "true" ]]; then
		local release
		release="$(helm list --all --short --namespace kube-system --filter '^osac-debug-scc$' 2>/dev/null)"
		if [[ -n "${release}" ]]; then
			make -C "${INSTALLER_DIR}" disable-debug KUBECONFIG="${KUBECONFIG}" >/dev/null 2>&1
		else
			local owner
			owner="$(kubectl get scc osac-remote-debug \
				-o jsonpath='{.metadata.labels.app\.kubernetes\.io/instance}' 2>/dev/null)"
			if [[ "${owner}" == "osac-debug-scc" ]]; then
				kubectl delete scc osac-remote-debug >/dev/null 2>&1
			fi
		fi
	fi
	if [[ "${namespace_created}" == "true" ]]; then
		kubectl delete namespace "${TEST_NAMESPACE}" --wait=false --ignore-not-found >/dev/null 2>&1
		wait_for_namespace_cleanup "${TEST_NAMESPACE}" 90 >/dev/null 2>&1
	fi
  exit "${result}"
}
trap cleanup EXIT

kubectl create namespace "${TEST_NAMESPACE}"
namespace_created=true
kubectl create serviceaccount debug --namespace "${TEST_NAMESPACE}"

echo "Installing the managed SCC for system:serviceaccount:${TEST_NAMESPACE}:debug..."
scc_enable_attempted=true
make -C "${INSTALLER_DIR}" enable-debug NS="${TEST_NAMESPACE}" KUBECONFIG="${KUBECONFIG}"

echo "Creating a Pod that requires SYS_PTRACE and unconfined seccomp..."
kubectl apply --namespace "${TEST_NAMESPACE}" -f - <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: ${TEST_POD}
spec:
  serviceAccountName: debug
  restartPolicy: Never
  securityContext:
    runAsNonRoot: true
    seccompProfile:
      type: Unconfined
  containers:
  - name: admission-probe
    image: ${TEST_IMAGE}
    imagePullPolicy: IfNotPresent
    command: ["/bin/sh", "-c", "exec /usr/bin/sleep 3600"]
    securityContext:
      allowPrivilegeEscalation: false
      readOnlyRootFilesystem: true
      capabilities:
        drop: [ALL]
        add: [SYS_PTRACE]
EOF

kubectl wait --for=condition=Ready "pod/${TEST_POD}" \
  --namespace "${TEST_NAMESPACE}" --timeout=180s

assigned_scc="$(kubectl get pod "${TEST_POD}" --namespace "${TEST_NAMESPACE}" \
  -o jsonpath='{.metadata.annotations.openshift\.io/scc}')"
if [[ "${assigned_scc}" != "osac-remote-debug" ]]; then
  echo "ERROR: expected SCC 'osac-remote-debug', Pod was admitted with '${assigned_scc}'." >&2
  exit 1
fi

assigned_user="$(kubectl get scc osac-remote-debug -o jsonpath='{.users[*]}')"
if [[ "${assigned_user}" != "system:serviceaccount:${TEST_NAMESPACE}:debug" ]]; then
  echo "ERROR: SCC user grant is not scoped to the test ServiceAccount: ${assigned_user}" >&2
  exit 1
fi
assigned_groups="$(kubectl get scc osac-remote-debug -o jsonpath='{.groups[*]}')"
if [[ -n "${assigned_groups}" ]]; then
  echo "ERROR: SCC unexpectedly grants access to groups: ${assigned_groups}" >&2
  exit 1
fi

echo "Verifying disable-debug is blocked while the Pod uses the SCC..."
if disable_output="$(make -C "${INSTALLER_DIR}" disable-debug KUBECONFIG="${KUBECONFIG}" 2>&1)"; then
  echo "ERROR: disable-debug removed the SCC while the test Pod was using it." >&2
  exit 1
fi
if [[ "${disable_output}" != *"cannot disable-debug"* ||
      "${disable_output}" != *"${TEST_NAMESPACE}/${TEST_POD}"* ]]; then
  echo "ERROR: disable-debug failed for an unexpected reason:" >&2
  printf '%s\n' "${disable_output}" >&2
  exit 1
fi
helm status osac-debug-scc --namespace kube-system >/dev/null
kubectl get scc osac-remote-debug >/dev/null

echo "Deleting the probe Pod, then verifying disable-debug removes the SCC..."
kubectl delete pod "${TEST_POD}" --namespace "${TEST_NAMESPACE}" --wait=true --timeout=60s
make -C "${INSTALLER_DIR}" disable-debug KUBECONFIG="${KUBECONFIG}"
scc_enable_attempted=false

remaining_scc="$(kubectl get scc osac-remote-debug --ignore-not-found -o name)"
if [[ -n "${remaining_scc}" ]]; then
  echo "ERROR: SCC 'osac-remote-debug' remains after disable-debug." >&2
  exit 1
fi
echo "OpenShift remote-debug SCC admission and removal checks passed."
