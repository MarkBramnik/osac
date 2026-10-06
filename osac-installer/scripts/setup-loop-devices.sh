#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib.sh"

readonly DEFAULT_BACKING_DIR=/var/lib/osac-lvms
readonly DEFAULT_LOOP_SIZE_GIB=5
readonly DEFAULT_DEBUG_NAMESPACE=default

KUBECONFIG=${KUBECONFIG:-}
BACKING_DIR=${BACKING_DIR:-$DEFAULT_BACKING_DIR}
LOOP_SIZE_GIB=${LOOP_SIZE_GIB:-$DEFAULT_LOOP_SIZE_GIB}
DEBUG_NAMESPACE=${DEBUG_NAMESPACE:-$DEFAULT_DEBUG_NAMESPACE}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<EOF
Usage:
  $0 --kubeconfig PATH [options]

Creates and attaches /dev/loop10 and /dev/loop11 on every Ready worker node.
Command-line options override their corresponding environment variables.

Options / environment variables:
  --kubeconfig PATH       KUBECONFIG; required unless already exported
  --backing-dir PATH      BACKING_DIR; default: ${DEFAULT_BACKING_DIR}
  --loop-size-gib SIZE    LOOP_SIZE_GIB; default: ${DEFAULT_LOOP_SIZE_GIB}
  --debug-namespace NAME  DEBUG_NAMESPACE; default: ${DEFAULT_DEBUG_NAMESPACE}

Example:
  $0 --kubeconfig ~/.kube/roy-oc-cluster.kubeconfig --loop-size-gib 5
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --help|-h)
      usage
      exit 0
      ;;
    --kubeconfig|--backing-dir|--loop-size-gib|--debug-namespace)
      [[ $# -ge 2 ]] || die "Option $1 requires a value. Use --help for usage."
      option=$1
      value=$2
      shift 2
      case "$option" in
        --kubeconfig) KUBECONFIG=$value ;;
        --backing-dir) BACKING_DIR=$value ;;
        --loop-size-gib) LOOP_SIZE_GIB=$value ;;
        --debug-namespace) DEBUG_NAMESPACE=$value ;;
      esac
      ;;
    --kubeconfig=*) KUBECONFIG=${1#*=}; shift ;;
    --backing-dir=*) BACKING_DIR=${1#*=}; shift ;;
    --loop-size-gib=*) LOOP_SIZE_GIB=${1#*=}; shift ;;
    --debug-namespace=*) DEBUG_NAMESPACE=${1#*=}; shift ;;
    *) die "Unknown option or positional argument: $1. Use --help for usage." ;;
  esac
done

[[ -n ${KUBECONFIG:-} ]] || die "Set KUBECONFIG to the target OpenShift cluster kubeconfig."
[[ -r $KUBECONFIG ]] || die "KUBECONFIG is not readable: $KUBECONFIG"
command -v oc >/dev/null 2>&1 || die "oc is required but was not found in PATH."

[[ $LOOP_SIZE_GIB =~ ^[1-9][0-9]*$ ]] || die "LOOP_SIZE_GIB must be a positive integer."
[[ $BACKING_DIR == /* ]] || die "BACKING_DIR must be an absolute host path."
[[ $BACKING_DIR != *[[:space:]]* ]] || die "BACKING_DIR must not contain whitespace."
[[ $BACKING_DIR != *:* ]] || die "BACKING_DIR must not contain a colon."
[[ $BACKING_DIR != *plugins/kubernetes.io* ]] || die "LVMS filters loop devices backed from Kubernetes plugin paths."

export KUBECONFIG
retry_until 30 2 'oc get namespace "${DEBUG_NAMESPACE}" >/dev/null 2>&1' \
  || die "Debug namespace does not exist or is not accessible: $DEBUG_NAMESPACE"

cluster_server=$(oc whoami --show-server) || die "Could not contact the API server using KUBECONFIG."
printf 'Target cluster: %s\n' "$cluster_server"

node_names=$(oc get nodes -l node-role.kubernetes.io/worker \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}') \
  || die "Could not list worker nodes."
nodes=()
while IFS= read -r node; do
  [[ -n $node ]] && nodes+=("$node")
done <<< "$node_names"

[[ ${#nodes[@]} -gt 0 ]] || die "No worker nodes were found."

for node in "${nodes[@]}"; do
  ready=$(oc get node "$node" -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}') \
    || die "Could not read readiness for node $node."
  [[ $ready == True ]] || die "Node $node is not Ready; refusing partial setup."
done

host_script='
set -eu

mode=$1
backing_dir=$2
size_gib=$3
size_bytes=$((size_gib * 1024 * 1024 * 1024))
device_1=/dev/loop10
device_2=/dev/loop11
file_1=$backing_dir/loopback1.img
file_2=$backing_dir/loopback2.img

fail() {
  printf "ERROR: %s\n" "$*" >&2
  exit 1
}

mapped_file_for() {
  losetup --list --noheadings --output BACK-FILE "$1" 2>/dev/null \
    | sed -e "s/^[[:space:]]*//" -e "s/[[:space:]]*$//"
}

validate_device_node() {
  node_device=$1
  case "$node_device" in
    /dev/loop10) expected_minor=a ;;
    /dev/loop11) expected_minor=b ;;
    *) fail "Unexpected loop device path: $node_device" ;;
  esac

  if [ -L "$node_device" ]; then
    fail "$node_device must be a device node, not a symlink."
  fi
  if [ -e "$node_device" ]; then
    [ -b "$node_device" ] || fail "$node_device exists but is not a block-device node."
    actual_device=$(stat -c "%t:%T" "$node_device") \
      || fail "Could not inspect device numbers for $node_device."
    [ "$actual_device" = "7:$expected_minor" ] \
      || fail "$node_device has device numbers $actual_device; expected 7:$expected_minor."
  fi
}

ensure_device_node() {
  node_device=$1
  validate_device_node "$node_device"
  if [ -e "$node_device" ]; then
    return 0
  fi

  case "$node_device" in
    /dev/loop10) node_minor=10 ;;
    /dev/loop11) node_minor=11 ;;
    *) fail "Unexpected loop device path: $node_device" ;;
  esac

  printf "Creating loop device node %s (major 7, minor %s)\n" "$node_device" "$node_minor"
  mknod "$node_device" b 7 "$node_minor" || fail "Could not create device node $node_device."
  validate_device_node "$node_device"
}

check_pair() {
  pair_device=$1
  pair_file=$2

  validate_device_node "$pair_device"

  pair_mapping=$(mapped_file_for "$pair_device" || true)
  if [ -n "$pair_mapping" ] && [ "$pair_mapping" != "$pair_file" ]; then
    fail "$pair_device is already attached to $pair_mapping, not $pair_file; leaving it untouched."
  fi

  if [ -L "$pair_file" ]; then
    fail "Backing file must not be a symlink: $pair_file"
  fi
  if [ -e "$pair_file" ]; then
    [ -f "$pair_file" ] || fail "Backing path exists but is not a regular file: $pair_file"
    pair_actual_size=$(stat -c "%s" "$pair_file") || fail "Could not inspect $pair_file"
    [ "$pair_actual_size" -eq "$size_bytes" ] \
      || fail "$pair_file is ${pair_actual_size} bytes; expected ${size_bytes}. It was not overwritten."
  elif [ -n "$pair_mapping" ]; then
    fail "$pair_device refers to $pair_file, but that file is not present on the host."
  fi

  other_devices=$(losetup -j "$pair_file" 2>/dev/null | cut -d: -f1 || true)
  if [ -n "$other_devices" ] && [ "$other_devices" != "$pair_device" ]; then
    fail "$pair_file is already attached to $other_devices; refusing a duplicate loop attachment."
  fi
}

if [ "$mode" = preflight ]; then
  missing_files=0
  check_pair "$device_1" "$file_1"
  [ -f "$file_1" ] || missing_files=$((missing_files + 1))
  check_pair "$device_2" "$file_2"
  [ -f "$file_2" ] || missing_files=$((missing_files + 1))

  probe_dir=$backing_dir
  while [ ! -d "$probe_dir" ]; do
    parent_dir=$(dirname "$probe_dir")
    [ "$parent_dir" != "$probe_dir" ] || fail "Could not find an existing directory for $backing_dir"
    probe_dir=$parent_dir
  done
  available_kib=$(df -Pk "$probe_dir" | awk "NR == 2 {print \$4}")
  case "$available_kib" in
    ""|*[!0-9]*) fail "Could not determine free space for $probe_dir" ;;
  esac
  required_kib=$((missing_files * size_gib * 1024 * 1024 + 1024 * 1024))
  [ "$available_kib" -ge "$required_kib" ] \
    || fail "$probe_dir has ${available_kib} KiB free; ${required_kib} KiB is required for missing files plus a 1 GiB reserve."

  printf "Preflight OK: %s and %s are available; %s KiB free on %s.\n" \
    "$device_1" "$device_2" "$available_kib" "$probe_dir"
  exit 0
fi

[ "$mode" = setup ] || fail "Unknown operation mode: $mode"
mkdir -p "$backing_dir"

for pair in "$device_1:$file_1" "$device_2:$file_2"; do
  pair_device=${pair%%:*}
  pair_file=${pair#*:}
  ensure_device_node "$pair_device"
  check_pair "$pair_device" "$pair_file"

  if [ ! -e "$pair_file" ]; then
    printf "Creating %s GiB backing file %s\n" "$size_gib" "$pair_file"
    dd if=/dev/zero of="$pair_file" bs=1G count="$size_gib" conv=fsync status=progress
  fi

  pair_mapping=$(mapped_file_for "$pair_device" || true)
  if [ -z "$pair_mapping" ]; then
    losetup "$pair_device" "$pair_file"
  fi

  pair_mapping=$(mapped_file_for "$pair_device" || true)
  [ "$pair_mapping" = "$pair_file" ] \
    || fail "Verification failed: $pair_device maps to $pair_mapping, expected $pair_file."
  printf "Attached %s -> %s\n" "$pair_device" "$pair_file"
done
'

run_on_node() {
  local node=$1
  local mode=$2
  printf '\n== %s: %s ==\n' "$node" "$mode"
  # chroot uses host binaries/root; nsenter ensures losetup acts on the node's
  # namespaces rather than the temporary oc debug pod's namespaces.
  oc debug "node/$node" --to-namespace="$DEBUG_NAMESPACE" -- \
    chroot /host /usr/bin/nsenter -a -t 1 -- /bin/sh -c "$host_script" setup-loop-devices "$mode" "$BACKING_DIR" "$LOOP_SIZE_GIB"
}

printf 'Preflighting %s worker nodes; each needs up to %s GiB under %s.\n' \
  "${#nodes[@]}" "$((LOOP_SIZE_GIB * 2))" "$BACKING_DIR"
for node in "${nodes[@]}"; do
  run_on_node "$node" preflight
done

for node in "${nodes[@]}"; do
  run_on_node "$node" setup
done

printf '\nLoop-device setup complete on %s nodes.\n' "${#nodes[@]}"
printf 'The backing files are host-local and survive a reboot of the same node, but not node replacement.\n'
