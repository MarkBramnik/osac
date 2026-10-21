#!/bin/sh
set -eu

debug_continue=${OSAC_DEBUG_CONTINUE:-false}
debug_listen=${OSAC_DEBUG_LISTEN_ADDRESS:-127.0.0.1:2345}
debugger=${OSAC_DEBUGGER_BIN:-/usr/local/bin/dlv}
program=${FULFILLMENT_SERVICE_BIN:-/usr/local/bin/fulfillment-service}

case "$debug_continue" in
true)
	exec "$debugger" \
		--headless \
		--listen="$debug_listen" \
		--api-version=2 \
		--only-same-user=false \
		exec --continue --accept-multiclient "$program" -- "$@"
	;;
false)
	exec "$debugger" \
		--headless \
		--listen="$debug_listen" \
		--api-version=2 \
		--only-same-user=false \
		exec "$program" -- "$@"
	;;
*)
	echo "OSAC_DEBUG_CONTINUE must be true or false" >&2
	exit 2
	;;
esac
