#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ENTRYPOINT="${SCRIPT_DIR}/debug-entrypoint.sh"
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

OSAC_DEBUG_CONTINUE=true OSAC_DEBUG_LISTEN_ADDRESS=127.0.0.1:2345 \
	"${ENTRYPOINT}" start grpc-server --log-level=debug
grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--only-same-user=false' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--listen=127.0.0.1:2345' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '/usr/local/bin/fulfillment-service' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- 'start' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- 'grpc-server' "${OSAC_DEBUG_TEST_ARGS}"
grep -Fxq -- '--log-level=debug' "${OSAC_DEBUG_TEST_ARGS}"

OSAC_DEBUG_CONTINUE=false "${ENTRYPOINT}" start controller
if grep -Fxq -- '--continue' "${OSAC_DEBUG_TEST_ARGS}"; then
	echo 'debug entrypoint unexpectedly continued the service' >&2
	exit 1
fi
if grep -Fxq -- '--accept-multiclient' "${OSAC_DEBUG_TEST_ARGS}"; then
	echo 'paused debug entrypoint unexpectedly enabled multiple clients' >&2
	exit 1
fi
grep -Fxq -- 'controller' "${OSAC_DEBUG_TEST_ARGS}"

if OSAC_DEBUG_CONTINUE=invalid "${ENTRYPOINT}" >/dev/null 2>&1; then
	echo 'debug entrypoint accepted an invalid OSAC_DEBUG_CONTINUE value' >&2
	exit 1
fi
