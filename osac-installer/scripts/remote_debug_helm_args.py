#!/usr/bin/env python3
"""Build Helm overrides for the selected remote-debug service set."""

from __future__ import annotations

import argparse
import shlex
import sys

SUPPORTED_SERVICES = {"osac-operator", "fulfillment-service", "csi-driver"}


class ConfigurationError(ValueError):
    """Raised when service or image arguments are invalid."""


def parse_bool(value: str, name: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigurationError(f"{name} must be true or false")


def parse_services(value: str) -> set[str]:
    names = [part.strip() for part in value.split(",") if part.strip()]
    unknown = sorted(set(names) - SUPPORTED_SERVICES)
    if unknown:
        raise ConfigurationError(
            f"unsupported service(s): {', '.join(unknown)}; supported: "
            f"{', '.join(sorted(SUPPORTED_SERVICES))}"
        )
    if len(names) != len(set(names)):
        raise ConfigurationError("SERVICES must not contain duplicates")
    return set(names)


def split_tagged_image(image: str, variable: str) -> tuple[str, str]:
    if any(character.isspace() for character in image) or "@" in image:
        raise ConfigurationError(
            f"{variable} must be a tagged image reference (repository:tag)"
        )
    slash = image.rfind("/")
    colon = image.rfind(":")
    if colon <= slash or colon == len(image) - 1:
        raise ConfigurationError(
            f"{variable} must include a tag (repository:tag); got {image!r}"
        )
    repository, tag = image[:colon], image[colon + 1 :]
    if not repository or not tag or "," in image:
        raise ConfigurationError(f"invalid {variable}: {image!r}")
    return repository, tag


def _set(name: str, value: str) -> list[str]:
    return ["--set", f"{name}={value}"]


def _set_string(name: str, value: str) -> list[str]:
    return ["--set-string", f"{name}={value}"]


def build_helm_args(
    *,
    services_value: str,
    debug_value: str,
    platform: str,
    debug_continue_value: str,
    fulfillment_debug_continue_value: str,
    osac_operator_image: str = "",
    fulfillment_service_image: str = "",
    rollout_token: str = "",
) -> list[str]:
    services = parse_services(services_value)
    debug_enabled = parse_bool(debug_value, "DEBUG")
    debug_continue = parse_bool(debug_continue_value, "DEBUG_CONTINUE")
    fulfillment_debug_continue = parse_bool(
        fulfillment_debug_continue_value, "FULFILLMENT_DEBUG_CONTINUE"
    )
    if platform not in {"kind", "openshift"}:
        raise ConfigurationError("PLATFORM must be kind or openshift")
    if debug_enabled and not services:
        raise ConfigurationError("SERVICES is required when DEBUG=true")

    operator_debug = debug_enabled and "osac-operator" in services
    fulfillment_debug = debug_enabled and "fulfillment-service" in services
    if operator_debug and not osac_operator_image:
        raise ConfigurationError(
            "OSAC_OPERATOR_IMAGE is required when debugging osac-operator"
        )
    if fulfillment_debug and not fulfillment_service_image:
        raise ConfigurationError(
            "FULFILLMENT_SERVICE_IMAGE is required when debugging fulfillment-service"
        )

    image_pull_policy = "IfNotPresent" if platform == "kind" else "Always"
    fs_group = "1001" if platform == "kind" else "auto"
    args: list[str] = []

    args += _set("operator.debug.enabled", str(operator_debug).lower())
    args += _set("operator.debug.continue", str(debug_continue).lower())
    args += _set("operator.debug.fsGroup", fs_group)
    args += _set("operator.debug.imagePullPolicy", image_pull_policy)
    if "osac-operator" in services and rollout_token:
        args += _set_string("operator.debug.rolloutToken", rollout_token)
    if operator_debug:
        args += _set_string("operator.debug.image", osac_operator_image)
    elif "osac-operator" in services and osac_operator_image:
        repository, tag = split_tagged_image(
            osac_operator_image, "OSAC_OPERATOR_IMAGE"
        )
        args += _set_string("operator.image.repository", repository)
        args += _set_string("operator.image.tag", tag)

    args += _set("service.debug.enabled", str(fulfillment_debug).lower())
    args += _set(
        "service.debug.continue", str(fulfillment_debug_continue).lower()
    )
    args += _set("service.debug.imagePullPolicy", image_pull_policy)
    if "fulfillment-service" in services and rollout_token:
        args += _set_string("service.debug.rolloutToken", rollout_token)
    if fulfillment_debug:
        args += _set_string("service.debug.image", fulfillment_service_image)
    else:
        args += _set_string("service.debug.image", "")
        if "fulfillment-service" in services and fulfillment_service_image:
            repository, tag = split_tagged_image(
                fulfillment_service_image, "FULFILLMENT_SERVICE_IMAGE"
            )
            args += _set_string("service.images.service.repository", repository)
            args += _set_string("service.images.service.tag", tag)

    return args


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--services", required=True)
    parser.add_argument("--debug", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--debug-continue", required=True)
    parser.add_argument("--fulfillment-debug-continue", required=True)
    parser.add_argument("--osac-operator-image", default="")
    parser.add_argument("--fulfillment-service-image", default="")
    parser.add_argument("--rollout-token", default="")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--helm-args", action="store_true")
    args = parser.parse_args()

    try:
        helm_args = build_helm_args(
            services_value=args.services,
            debug_value=args.debug,
            platform=args.platform,
            debug_continue_value=args.debug_continue,
            fulfillment_debug_continue_value=args.fulfillment_debug_continue,
            osac_operator_image=args.osac_operator_image,
            fulfillment_service_image=args.fulfillment_service_image,
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
