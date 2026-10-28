#!/usr/bin/env python3
"""Build Helm overrides for the standalone CSI driver release."""

from __future__ import annotations

import argparse
import shlex
import sys

from remote_debug_helm_args import ConfigurationError, parse_bool, split_tagged_image


def build_helm_args(
    *,
    platform: str,
    namespace: str,
    debug_value: str,
    debug_continue_value: str,
    node_continue_value: str,
    image: str = "",
    rollout_token: str = "",
) -> list[str]:
    debug_enabled = parse_bool(debug_value, "DEBUG")
    debug_continue = parse_bool(debug_continue_value, "CSI_DRIVER_DEBUG_CONTINUE")
    node_continue = parse_bool(node_continue_value, "CSI_DRIVER_NODE_DEBUG_CONTINUE")
    if platform not in {"kind", "openshift"}:
        raise ConfigurationError("PLATFORM must be kind or openshift")
    if namespace != "osac-csi":
        raise ConfigurationError(
            "CSI_DRIVER_NAMESPACE must be osac-csi for the current CSI chart"
        )
    if debug_enabled and not image:
        raise ConfigurationError(
            "CSI_DRIVER_SERVICE_IMAGE is required when debugging csi-driver"
        )

    pull_policy = "IfNotPresent" if platform == "kind" else "Always"
    fs_group = "1001" if platform == "kind" else "auto"
    args = [
        "--set-string",
        f"namespace={namespace}",
        "--set",
        f"image.pullPolicy={pull_policy}",
        "--set",
        f"debug.enabled={str(debug_enabled).lower()}",
        "--set",
        f"debug.imagePullPolicy={pull_policy}",
        "--set-string",
        f"debug.fsGroup={fs_group}",
        "--set",
        f"debug.continue={str(debug_continue).lower()}",
        "--set",
        f"debug.nodeContinue={str(node_continue).lower()}",
    ]
    if rollout_token:
        args += ["--set-string", f"debug.rolloutToken={rollout_token}"]
    if debug_enabled:
        args += ["--set-string", f"debug.image={image}"]
    elif image:
        repository, tag = split_tagged_image(image, "CSI_DRIVER_SERVICE_IMAGE")
        args += ["--set-string", f"image.repository={repository}"]
        args += ["--set-string", f"image.tag={tag}"]
    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--debug", required=True)
    parser.add_argument("--debug-continue", required=True)
    parser.add_argument("--node-continue", required=True)
    parser.add_argument("--image", default="")
    parser.add_argument("--rollout-token", default="")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--helm-args", action="store_true")
    args = parser.parse_args()
    try:
        helm_args = build_helm_args(
            platform=args.platform,
            namespace=args.namespace,
            debug_value=args.debug,
            debug_continue_value=args.debug_continue,
            node_continue_value=args.node_continue,
            image=args.image,
            rollout_token=args.rollout_token,
        )
    except ConfigurationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    if args.helm_args:
        print(shlex.join(helm_args))
    elif not args.validate_only:
        parser.error("specify --validate-only or --helm-args")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
