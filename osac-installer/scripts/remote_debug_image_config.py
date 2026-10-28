#!/usr/bin/env python3
"""Resolve component image references for the focused installer workflow."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from remote_debug_helm_args import ConfigurationError, parse_bool, parse_services, split_tagged_image

IMAGE_CONFIG_DEFAULT = "~/.config/osac/image-mappings.json"
IMAGE_REFERENCE_CHARS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
SERVICE_IMAGE_VARIABLES = {
    "osac-operator": "OSAC_OPERATOR_IMAGE",
    "fulfillment-service": "FULFILLMENT_SERVICE_IMAGE",
    "csi-driver": "CSI_DRIVER_SERVICE_IMAGE",
}


@dataclass(frozen=True)
class ResolvedImages:
    osac_operator: str = ""
    fulfillment_service: str = ""
    csi_driver: str = ""


def _validate_image(value: str, variable: str) -> str:
    if not IMAGE_REFERENCE_CHARS.fullmatch(value):
        raise ConfigurationError(
            f"{variable} must be a tagged image reference using only "
            "letters, digits, '.', '_', '-', '/', and ':'"
        )
    split_tagged_image(value, variable)
    return value


def _load_config(path_value: str) -> dict[str, dict[str, str]]:
    path = Path(path_value).expanduser()
    if not path.exists():
        return {}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(
            f"cannot read image config {path}: {error}"
        ) from error

    if (
        not isinstance(document, dict)
        or type(document.get("version")) is not int
        or document["version"] != 1
    ):
        raise ConfigurationError(f"{path} must be a JSON object with version: 1")
    if set(document) != {"version", "images"}:
        raise ConfigurationError(
            f"{path} may contain only the 'version' and 'images' fields"
        )

    images = document["images"]
    if not isinstance(images, dict) or set(images) - {"normal", "debug"}:
        raise ConfigurationError(
            f"{path} images must contain only 'normal' and/or 'debug' objects"
        )

    result: dict[str, dict[str, str]] = {}
    for mode, mode_images in images.items():
        if not isinstance(mode_images, dict):
            raise ConfigurationError(f"{path} images.{mode} must be an object")
        unknown = sorted(set(mode_images) - set(SERVICE_IMAGE_VARIABLES))
        if unknown:
            raise ConfigurationError(
                f"{path} images.{mode} has unsupported service(s): "
                f"{', '.join(unknown)}"
            )
        result[mode] = {}
        for service, image in mode_images.items():
            variable = SERVICE_IMAGE_VARIABLES[service]
            if not isinstance(image, str):
                raise ConfigurationError(
                    f"{path} images.{mode}.{service} must be a string"
                )
            result[mode][service] = _validate_image(image, variable)
    return result


def resolve_images(
    *,
    config_path: str,
    services_value: str,
    debug_value: str,
    build_value: str,
    push_value: str,
    platform: str,
    osac_operator_image: str = "",
    fulfillment_service_image: str = "",
    csi_driver_service_image: str = "",
) -> ResolvedImages:
    services = parse_services(services_value)
    debug_enabled = parse_bool(debug_value, "DEBUG")
    build_enabled = parse_bool(build_value, "BUILD")
    push_enabled = parse_bool(push_value, "PUSH")
    if platform not in {"kind", "openshift"}:
        raise ConfigurationError("PLATFORM must be kind or openshift")

    config = _load_config(config_path)
    mode = "debug" if debug_enabled else "normal"
    configured = config.get(mode, {})
    cli_images = {
        "osac-operator": osac_operator_image,
        "fulfillment-service": fulfillment_service_image,
        "csi-driver": csi_driver_service_image,
    }
    resolved: dict[str, str] = {}
    for service, variable in SERVICE_IMAGE_VARIABLES.items():
        value = cli_images[service] or configured.get(service, "")
        resolved[service] = _validate_image(value, variable) if value else ""

    if build_enabled and services:
        if push_enabled:
            for service in sorted(services):
                if not resolved[service]:
                    variable = SERVICE_IMAGE_VARIABLES[service]
                    raise ConfigurationError(
                        f"{variable} is required when BUILD=true and PUSH=true; "
                        "set it on the command line or in "
                        f"{Path(config_path).expanduser()}"
                    )
        elif platform != "kind":
            raise ConfigurationError(
                "BUILD=true PUSH=false creates a local image; use PLATFORM=kind "
                "or push an image for OpenShift"
            )

    if debug_enabled and platform == "openshift":
        for service in sorted(services):
            if not resolved[service]:
                variable = SERVICE_IMAGE_VARIABLES[service]
                raise ConfigurationError(
                    f"{variable} is required when debugging {service} on OpenShift"
                )

    return ResolvedImages(
        osac_operator=resolved["osac-operator"],
        fulfillment_service=resolved["fulfillment-service"],
        csi_driver=resolved["csi-driver"],
    )


def resolve_from_environment() -> ResolvedImages:
    return resolve_images(
        config_path=os.environ.get("IMAGE_CONFIG", IMAGE_CONFIG_DEFAULT),
        services_value=os.environ.get("SERVICES", ""),
        debug_value=os.environ.get("DEBUG", "false"),
        build_value=os.environ.get("BUILD", "true"),
        push_value=os.environ.get("PUSH", "true"),
        platform=os.environ.get("PLATFORM", ""),
        osac_operator_image=os.environ.get("OSAC_OPERATOR_IMAGE", ""),
        fulfillment_service_image=os.environ.get("FULFILLMENT_SERVICE_IMAGE", ""),
        csi_driver_service_image=os.environ.get("CSI_DRIVER_SERVICE_IMAGE", ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--make-vars", action="store_true")
    mode.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if args.make_vars:
        try:
            configured = _load_config(
                os.environ.get("IMAGE_CONFIG", IMAGE_CONFIG_DEFAULT)
            )
        except ConfigurationError as error:
            print(f"ERROR: {error}", file=sys.stderr)
            return 2
        fields = []
        for mode_name in ("normal", "debug"):
            mode_images = configured.get(mode_name, {})
            fields.extend(
                (
                    f"{mode_name}-operator={mode_images.get('osac-operator', '-')}",
                    f"{mode_name}-fulfillment={mode_images.get('fulfillment-service', '-')}",
                    f"{mode_name}-csi-driver={mode_images.get('csi-driver', '-')}",
                )
            )
        print(" ".join(fields))
        return 0

    try:
        resolve_from_environment()
    except ConfigurationError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
