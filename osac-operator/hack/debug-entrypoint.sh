#!/bin/sh
set -eu

debug_continue=${OSAC_DEBUG_CONTINUE:-true}
# Delve's unauthenticated API is restricted to IPv4 loopback.
debug_listen=127.0.0.1:2345
debugger=${OSAC_DEBUGGER_BIN:-/usr/local/bin/dlv}
manager=/usr/local/bin/manager-debug

case "$debug_continue" in
true)
	exec "$debugger" \
		--headless \
		--listen="$debug_listen" \
		--api-version=2 \
		--only-same-user=false \
		exec --continue --accept-multiclient "$manager" -- "$@"
	;;
false)
	exec "$debugger" \
		--headless \
		--listen="$debug_listen" \
		--api-version=2 \
		--only-same-user=false \
		exec "$manager" -- "$@"
	;;
*)
	echo "OSAC_DEBUG_CONTINUE must be true or false" >&2
	exit 2
	;;
esac
