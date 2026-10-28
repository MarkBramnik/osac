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
export OSAC_DEBUGGEE_BIN=/usr/local/bin/osac-csi-driver

OSAC_DEBUG_CONTINUE=true "${ENTRYPOINT}" --csi-endpoint=unix:///csi/osac/csi.sock --node-id=controller
grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--only-same-user=false' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--listen=127.0.0.1:2345' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '/usr/local/bin/osac-csi-driver' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--csi-endpoint=unix:///csi/osac/csi.sock' "${OSAC_DEBUG_TEST_ARGS}"

OSAC_DEBUG_CONTINUE=false "${ENTRYPOINT}" --node-id=node
if grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"; then
	echo 'debug entrypoint unexpectedly continued the CSI driver' >&2
	exit 1
fi
grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--listen=127.0.0.1:2345' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--node-id=node' "${OSAC_DEBUG_TEST_ARGS}"

if OSAC_DEBUG_CONTINUE=invalid "${ENTRYPOINT}" >/dev/null 2>&1; then
	echo 'debug entrypoint accepted an invalid OSAC_DEBUG_CONTINUE value' >&2
	exit 1
fi
