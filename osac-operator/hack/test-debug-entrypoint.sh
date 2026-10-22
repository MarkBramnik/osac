#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
ENTRYPOINT="${REPO_ROOT}/tools/debug-entrypoint.sh"
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

mkdir -p "${tmpdir}/bin"
cat > "${tmpdir}/bin/dlv" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" > "$OSAC_DEBUG_TEST_ARGS"
EOF
chmod +x "${tmpdir}/bin/dlv"

export PATH="${tmpdir}/bin:${PATH}"
export OSAC_DEBUG_TEST_ARGS="${tmpdir}/args"
export OSAC_DEBUGGER_BIN="${tmpdir}/bin/dlv"
export OSAC_DEBUGGEE_BIN=/usr/local/bin/manager-debug

OSAC_DEBUG_CONTINUE=true OSAC_DEBUG_LISTEN_ADDRESS=0.0.0.0:2345 \
	"${ENTRYPOINT}" --leader-elect --metrics-bind-address=:8443
grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--only-same-user=false' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--listen=127.0.0.1:2345' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '/usr/local/bin/manager-debug' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--leader-elect' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--metrics-bind-address=:8443' "${OSAC_DEBUG_TEST_ARGS}"

OSAC_DEBUG_CONTINUE= "${ENTRYPOINT}" --leader-elect
grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"

OSAC_DEBUG_CONTINUE=false "${ENTRYPOINT}" --leader-elect
if grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"; then
	echo 'debug entrypoint unexpectedly continued the process' >&2
	exit 1
fi
if grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"; then
	echo 'debug entrypoint unexpectedly enabled multiple clients while paused' >&2
	exit 1
fi
grep -Fxq -- '--leader-elect' "${OSAC_DEBUG_TEST_ARGS}"

if OSAC_DEBUG_CONTINUE=invalid "${ENTRYPOINT}" >/dev/null 2>&1; then
	echo 'debug entrypoint accepted an invalid OSAC_DEBUG_CONTINUE value' >&2
	exit 1
fi
