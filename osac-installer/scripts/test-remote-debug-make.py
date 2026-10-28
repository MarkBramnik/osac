#!/usr/bin/env python3
"""Dry-run contracts for remote-debug image and installer targets."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from remote_debug_helm_args import ConfigurationError, build_helm_args
from remote_debug_csi_helm_args import (
    ConfigurationError as CSIDriverConfigurationError,
)
from remote_debug_csi_helm_args import build_helm_args as build_csi_helm_args
from remote_debug_image_config import ConfigurationError as ImageConfigError
from remote_debug_image_config import resolve_images

INSTALLER = Path(__file__).resolve().parents[1]


def make_command(
    target: str,
    *variables: str,
    dry_run: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["make", "--no-print-directory"]
    if dry_run:
        command.append("-n")
    command.extend(["-C", str(INSTALLER), target, *variables])
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    return subprocess.run(
        command, capture_output=True, text=True, check=False, env=process_env
    )


def dry_run(target: str, *variables: str) -> str:
    result = make_command(target, *variables)
    if result.returncode:
        raise AssertionError(f"Make dry-run failed:\n{result.stderr}")
    return result.stdout


def test_build_debug_operator_pushes_without_helm() -> None:
    image = "registry.example.test/osac/osac-operator:debug"
    output = dry_run(
        "build-osac-operator",
        "DEBUG=true",
        "PUSH=true",
        f"OSAC_OPERATOR_IMAGE={image}",
        "IMAGE_PLATFORM=linux/amd64",
    )
    assert "image-build-debug" in output
    assert "image-push-debug" in output
    assert f'DEBUG_IMG="{image}"' in output
    assert 'IMAGE_PLATFORM="linux/amd64"' in output
    assert "helm " not in output
    assert "kubectl " not in output
    assert "oc " not in output


def test_build_debug_fulfillment_pushes_shared_image_without_helm() -> None:
    image = "registry.example.test/osac/fulfillment-service:debug"
    output = dry_run(
        "build-fulfillment-service",
        "DEBUG=true",
        "PUSH=true",
        f"FULFILLMENT_SERVICE_IMAGE={image}",
        "IMAGE_PLATFORM=linux/amd64",
    )
    assert "image-build-debug" in output
    assert "--target runtime-debug" in output
    assert "image-push-debug" in output
    assert f'DEBUG_IMG="{image}"' in output
    assert 'IMAGE_PLATFORM="linux/amd64"' in output
    assert "helm " not in output
    assert "kubectl " not in output
    assert "oc " not in output


def test_build_normal_operator_cross_platform_without_push() -> None:
    output = dry_run(
        "build-osac-operator",
        "DEBUG=false",
        "PUSH=false",
        "IMAGE_PLATFORM=linux/amd64",
    )
    assert "image-build-debug" not in output
    assert "image-build" in output
    assert "image-push" not in output
    assert 'IMG="localhost/osac-operator:latest"' in output
    assert 'IMAGE_PLATFORM="linux/amd64"' in output
    assert "helm " not in output
    assert "kubectl " not in output


def test_build_normal_fulfillment_cross_platform_without_push() -> None:
    output = dry_run(
        "build-fulfillment-service",
        "DEBUG=false",
        "PUSH=false",
        "IMAGE_PLATFORM=linux/amd64",
    )
    assert "image-build-debug" not in output
    assert "image-build" in output
    assert "image-push" not in output
    assert 'IMG="localhost/fulfillment-service:latest"' in output
    assert 'IMAGE_PLATFORM="linux/amd64"' in output
    assert "helm " not in output
    assert "kubectl " not in output


def test_build_debug_csi_pushes_image_without_helm() -> None:
    image = "registry.example.test/osac/osac-csi-driver:debug"
    output = dry_run(
        "build-csi-driver",
        "DEBUG=true",
        "PUSH=true",
        f"CSI_DRIVER_SERVICE_IMAGE={image}",
        "IMAGE_PLATFORM=linux/amd64",
    )
    assert "image-build-debug" in output
    assert "--target runtime-debug" in output
    assert "image-push-debug" in output
    assert f'DEBUG_IMG="{image}"' in output
    assert 'IMAGE_PLATFORM="linux/amd64"' in output
    assert "helm " not in output
    assert "kubectl " not in output
    assert "oc " not in output


def test_push_requires_an_explicit_image_reference() -> None:
    result = make_command(
        "build-osac-operator",
        "DEBUG=true",
        "PUSH=true",
        dry_run=False,
    )
    assert result.returncode != 0
    assert "OSAC_OPERATOR_IMAGE is required when PUSH=true" in result.stderr
    assert "image-build-debug" not in result.stdout


def test_install_services_upgrades_once_with_both_debug_images() -> None:
    operator_image = "registry.example.test/osac/osac-operator:debug"
    fulfillment_image = "registry.example.test/osac/fulfillment-service:debug"
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "DEBUG=true",
        "DEBUG_CONTINUE=false",
        "FULFILLMENT_DEBUG_CONTINUE=true",
        "SERVICES=osac-operator,fulfillment-service",
        "IMAGE_PLATFORM=linux/amd64",
        f"OSAC_OPERATOR_IMAGE={operator_image}",
        f"FULFILLMENT_SERVICE_IMAGE={fulfillment_image}",
        "OPERATOR_VALUES_FILE=values/custom-osac.yaml",
        "FULFILLMENT_VALUES_FILE=values/custom-osac.yaml",
        "EXTRA_HELM_ARGS=--set=metering.enabled=false",
    )
    assert output.count("helm upgrade osac charts/osac") == 1
    assert output.count("image-build-debug") == 2
    assert output.count("image-push-debug") == 2
    assert output.count('IMAGE_PLATFORM="linux/amd64"') == 4
    assert output.index("helm status osac") < output.index("image-build-debug")
    assert output.index("image-build-debug") < output.index("helm upgrade osac")
    assert output.count("kind-runtime.sh load image-archive") == 2
    assert "-f values/dev/kind-instance.yaml" in output
    assert '-f "values/custom-osac.yaml"' in output
    assert "--set=metering.enabled=false" in output
    assert "--no-hooks" in output
    assert "--reuse-values" not in output
    assert "--skip-schema-validation" not in output
    assert "--set operator.debug.enabled=true" in output
    assert "--set operator.debug.continue=false" in output
    assert f"--set-string operator.debug.image={operator_image}" in output
    assert "--set service.debug.enabled=true" in output
    assert "--set service.debug.continue=true" in output
    assert f"--set-string service.debug.image={fulfillment_image}" in output
    operator_token_prefix = "--set-string operator.debug.rolloutToken="
    operator_token = output.split(operator_token_prefix, 1)[1].split()[0]
    assert operator_token.endswith("Z")
    assert f"--set-string service.debug.rolloutToken={operator_token}" in output
    assert output.index("scripts/remote_debug_helm_args.py") < output.index(
        "helm status osac"
    )


def test_install_services_openshift_checks_scc_and_sets_cluster_hosts() -> None:
    operator_image = "registry.example.test/osac/osac-operator:debug"
    fulfillment_image = "registry.example.test/osac/fulfillment-service:debug"
    output = dry_run(
        "install-services",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "DEBUG=true",
        "SERVICES=osac-operator,fulfillment-service",
        f"OSAC_OPERATOR_IMAGE={operator_image}",
        f"FULFILLMENT_SERVICE_IMAGE={fulfillment_image}",
        "FULFILLMENT_DOMAIN=apps.example.test",
    )
    assert output.count("helm upgrade osac charts/osac") == 1
    assert output.index("remote-debug SCC is not enabled") < output.index(
        "helm --repository-config /dev/null dependency build charts/osac"
    )
    assert "system:serviceaccount:osac:osac-operator-debug" in output
    assert "system:serviceaccount:osac:fulfillment-debug" in output
    assert "unexpected group grant" in output
    assert "global.osacDeploymentId=osac/osac" in output
    assert "service.externalHostname=fulfillment-api-osac.apps.example.test" in output
    assert "service.internalHostname=fulfillment-internal-api-osac.apps.example.test" in output
    assert "global.clusterDomain=apps.example.test" in output
    assert "ui.auth.oidcIssuerURL=https://keycloak-keycloak.apps.example.test/realms/osac" in output
    assert f"operator.debug.image={operator_image}" in output
    assert f"service.debug.image={fulfillment_image}" in output
    assert output.count("image-build-debug") == 2
    assert output.count("image-push-debug") == 2


def test_install_services_can_apply_normal_cross_built_images() -> None:
    operator_image = "registry.example.test/osac/osac-operator:1.2.3"
    fulfillment_image = "registry.example.test/osac/fulfillment-service:1.2.3"
    output = dry_run(
        "install-services",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "DEBUG=false",
        "SERVICES=osac-operator,fulfillment-service",
        f"OSAC_OPERATOR_IMAGE={operator_image}",
        f"FULFILLMENT_SERVICE_IMAGE={fulfillment_image}",
        "FULFILLMENT_DOMAIN=apps.example.test",
    )
    assert output.count("helm upgrade osac charts/osac") == 1
    assert "--no-hooks" in output
    assert "--skip-schema-validation" not in output
    assert "--set operator.debug.enabled=false" in output
    assert "--set service.debug.enabled=false" in output
    assert "--set-string operator.image.repository=registry.example.test/osac/osac-operator" in output
    assert "--set-string operator.image.tag=1.2.3" in output
    assert "--set-string service.images.service.repository=registry.example.test/osac/fulfillment-service" in output
    assert "--set-string service.images.service.tag=1.2.3" in output
    assert "--set-string operator.debug.rolloutToken=" in output
    assert "--set-string service.debug.rolloutToken=" in output
    assert "service.externalHostname=fulfillment-api-osac.apps.example.test" in output
    assert "run 'make enable-debug' first" not in output
    assert "helm upgrade osac" in output
    assert output.count("image-build ") == 2
    assert output.count("image-push ") == 2


def test_install_services_builds_only_selected_service() -> None:
    fulfillment_image = "registry.example.test/osac/fulfillment-service:dev"
    output = dry_run(
        "install-services",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "SERVICES=fulfillment-service",
        f"FULFILLMENT_SERVICE_IMAGE={fulfillment_image}",
        "FULFILLMENT_DOMAIN=apps.example.test",
    )
    assert "build-fulfillment-service" in output
    assert "build-osac-operator" not in output
    assert "image-build " in output
    assert "image-push " in output
    assert f'IMG="{fulfillment_image}"' in output
    assert "--set-string service.debug.rolloutToken=" in output
    assert "--set-string operator.debug.rolloutToken=" not in output


def test_install_services_delegates_csi_to_standalone_release_only() -> None:
    image = "registry.example.test/osac/osac-csi-driver:debug"
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "BUILD=false",
        "DEBUG=true",
        "SERVICES=csi-driver",
        f"CSI_DRIVER_SERVICE_IMAGE={image}",
        "CSI_DRIVER_NODE_DEBUG_CONTINUE=false",
    )
    assert "helm status osac --namespace" not in output
    assert "helm upgrade osac charts/osac" not in output
    assert "install-csi-driver" in output
    assert 'CSI_DRIVER_RELEASE="csi-driver"' in output
    assert 'CSI_DRIVER_NAMESPACE="osac-csi"' in output
    assert "CSI_DRIVER_NODE_DEBUG_CONTINUE=false" in output
    assert f'CSI_DRIVER_SERVICE_IMAGE="{image}"' in output


def test_install_csi_driver_builds_loads_and_upgrades_standalone_release() -> None:
    output = dry_run(
        "install-csi-driver",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "DEBUG=true",
        "PUSH=false",
        "CSI_DRIVER_NODE_DEBUG_CONTINUE=false",
        "IMAGE_PLATFORM=linux/amd64",
        "IMAGE_CONFIG=/no-such-image-config.json",
    )
    image = "localhost/osac-csi-driver:latest-debug"
    assert "image-build-debug" in output
    assert "image-push-debug" not in output
    assert f"kind-runtime.sh container save {image}" in output
    assert "helm upgrade --install \"csi-driver\" ../osac-csi-driver/charts/csi-driver" in output
    assert "--namespace \"osac-csi\"" in output
    assert "--reuse-values" in output
    assert "--set debug.enabled=true" in output
    assert "--set debug.continue=true" in output
    assert "--set debug.nodeContinue=false" in output
    assert f"--set-string debug.image={image}" in output
    assert "helm upgrade osac charts/osac" not in output
    assert "OSAC_DEBUG_SCC" not in output


def test_standalone_csi_build_requires_an_image_when_pushing() -> None:
    result = make_command(
        "install-csi-driver",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "BUILD=true",
        "PUSH=true",
        "DEBUG=true",
        "IMAGE_CONFIG=/no-such-image-config.json",
        dry_run=False,
    )
    assert result.returncode != 0
    assert "CSI_DRIVER_SERVICE_IMAGE is required" in result.stderr
    assert "helm upgrade --install" not in result.stdout


def test_install_csi_driver_defaults_to_installed_release_and_namespace() -> None:
    output = dry_run(
        "install-csi-driver",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "BUILD=false",
        "DEBUG=false",
        "IMAGE_CONFIG=/no-such-image-config.json",
    )
    assert 'helm upgrade --install "csi-driver" ../osac-csi-driver/charts/csi-driver' in output
    assert '--namespace "osac-csi"' in output
    assert "--reuse-values" in output


def test_install_services_build_false_skips_build_goals() -> None:
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "BUILD=false",
        "SERVICES=osac-operator,fulfillment-service",
        "OSAC_OPERATOR_IMAGE=registry.example.test/osac/osac-operator:prebuilt",
        "FULFILLMENT_SERVICE_IMAGE=registry.example.test/osac/fulfillment:prebuilt",
    )
    assert "helm upgrade osac charts/osac" in output
    assert "image-build" not in output
    assert "image-push" not in output
    assert "--set-string operator.debug.rolloutToken=" in output
    assert "--set-string service.debug.rolloutToken=" in output


def test_continue_shorthand_sets_both_runtime_modes() -> None:
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "BUILD=false",
        "DEBUG=true",
        "CONTINUE=false",
        "SERVICES=osac-operator,fulfillment-service",
        "OSAC_OPERATOR_IMAGE=registry.example.test/operator:debug",
        "FULFILLMENT_SERVICE_IMAGE=registry.example.test/fulfillment:debug",
    )
    assert "--set operator.debug.continue=false" in output
    assert "--set service.debug.continue=false" in output
    assert "--set-string operator.debug.rolloutToken=" in output
    assert "--set-string service.debug.rolloutToken=" in output


def test_continue_defaults_to_true_for_both_services() -> None:
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "DEBUG=true",
        "SERVICES=osac-operator,fulfillment-service",
        "OSAC_OPERATOR_IMAGE=registry.example.test/operator:debug",
        "FULFILLMENT_SERVICE_IMAGE=registry.example.test/fulfillment:debug",
    )
    assert "--set operator.debug.continue=true" in output
    assert "--set service.debug.continue=true" in output


def test_component_continue_override_does_not_change_other_service() -> None:
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "BUILD=false",
        "DEBUG=true",
        "DEBUG_CONTINUE=false",
        "SERVICES=osac-operator,fulfillment-service",
        "OSAC_OPERATOR_IMAGE=registry.example.test/operator:debug",
        "FULFILLMENT_SERVICE_IMAGE=registry.example.test/fulfillment:debug",
    )
    assert "--set operator.debug.continue=false" in output
    assert "--set service.debug.continue=true" in output


def test_install_services_resolves_config_and_cli_image_overrides() -> None:
    config = {
        "version": 1,
        "images": {
            "normal": {
                "osac-operator": "registry.example.test/osac/operator:normal",
                "fulfillment-service": "registry.example.test/osac/fulfillment:normal",
            },
            "debug": {
                "osac-operator": "registry.example.test/osac/operator:debug",
                "fulfillment-service": "registry.example.test/osac/fulfillment:debug",
            },
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "image 'refs.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        cli_operator_image = "quay.io/team/osac-operator:override"
        output = dry_run(
            "install-services",
            "PLATFORM=openshift",
            "PROFILE=vmaas-ci",
            "NS=osac",
            "DEBUG=true",
            "SERVICES=osac-operator,fulfillment-service",
            f"IMAGE_CONFIG={config_path}",
            f"OSAC_OPERATOR_IMAGE={cli_operator_image}",
            "FULFILLMENT_DOMAIN=apps.example.test",
        )

    assert f'DEBUG_IMG="{cli_operator_image}"' in output
    assert 'DEBUG_IMG="registry.example.test/osac/fulfillment:debug"' in output
    assert f"operator.debug.image={cli_operator_image}" in output
    assert "service.debug.image=registry.example.test/osac/fulfillment:debug" in output


def test_image_config_selects_debug_or_normal_and_cli_wins() -> None:
    config = {
        "version": 1,
        "images": {
            "normal": {"osac-operator": "registry.example.test/operator:normal"},
            "debug": {"osac-operator": "registry.example.test/operator:debug"},
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "images.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        normal = resolve_images(
            config_path=str(config_path),
            services_value="osac-operator",
            debug_value="false",
            build_value="false",
            push_value="true",
            platform="kind",
        )
        debug_override = resolve_images(
            config_path=str(config_path),
            services_value="osac-operator",
            debug_value="true",
            build_value="false",
            push_value="true",
            platform="openshift",
            osac_operator_image="quay.io/team/operator:cli",
        )

    assert normal.osac_operator == "registry.example.test/operator:normal"
    assert debug_override.osac_operator == "quay.io/team/operator:cli"


def test_csi_image_config_selects_debug_or_normal_and_cli_wins() -> None:
    config = {
        "version": 1,
        "images": {
            "normal": {"csi-driver": "registry.example.test/csi:normal"},
            "debug": {"csi-driver": "registry.example.test/csi:debug"},
        },
    }
    with tempfile.TemporaryDirectory() as directory:
        config_path = Path(directory) / "images.json"
        config_path.write_text(json.dumps(config), encoding="utf-8")
        normal = resolve_images(
            config_path=str(config_path),
            services_value="csi-driver",
            debug_value="false",
            build_value="false",
            push_value="true",
            platform="kind",
        )
        debug_override = resolve_images(
            config_path=str(config_path),
            services_value="csi-driver",
            debug_value="true",
            build_value="false",
            push_value="true",
            platform="openshift",
            csi_driver_service_image="quay.io/team/csi:cli",
        )

    assert normal.csi_driver == "registry.example.test/csi:normal"
    assert debug_override.csi_driver == "quay.io/team/csi:cli"


def test_local_builds_are_rejected_for_openshift() -> None:
    try:
        resolve_images(
            config_path="/no-such-image-config.json",
            services_value="osac-operator",
            debug_value="false",
            build_value="true",
            push_value="false",
            platform="openshift",
        )
    except ImageConfigError as error:
        assert "use PLATFORM=kind" in str(error)
    else:
        raise AssertionError("a local-only OpenShift build was accepted")


def test_kind_local_build_uses_and_loads_default_tags() -> None:
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "BUILD=true",
        "PUSH=false",
        "DEBUG=false",
        "SERVICES=fulfillment-service",
        "IMAGE_CONFIG=/no-such-image-config.json",
    )
    assert 'IMG="localhost/fulfillment-service:latest"' in output
    assert "kind-runtime.sh container save localhost/fulfillment-service:latest" in output
    assert "--set-string service.images.service.repository=localhost/fulfillment-service" in output
    assert "--set-string service.images.service.tag=latest" in output


def test_install_services_requires_images_for_default_push_builds() -> None:
    result = make_command(
        "install-services",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "SERVICES=osac-operator",
        "IMAGE_CONFIG=/no-such-image-config.json",
        dry_run=False,
    )
    assert result.returncode != 0
    assert "OSAC_OPERATOR_IMAGE is required when BUILD=true and PUSH=true" in result.stderr
    assert "helm status osac" not in result.stdout


def test_install_services_supports_dev_full_overlays() -> None:
    output = dry_run(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev-full",
        "NS=osac",
        "DEBUG=false",
    )
    assert "-f values/dev/kind-instance.yaml" in output
    assert "-f values/dev/kind-instance-devfull.yaml" in output
    assert "--set-string operator.aap.url=http://awx-service.osac.svc.cluster.local:80/api" in output


def test_install_services_rejects_unknown_services_before_cluster_calls() -> None:
    result = make_command(
        "install-services",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "DEBUG=true",
        "SERVICES=fulfillment-service,unknown-service",
        "FULFILLMENT_SERVICE_IMAGE=registry.example.test/osac/fulfillment:debug",
        dry_run=False,
    )
    assert result.returncode != 0
    assert "unsupported service(s): unknown-service" in result.stderr
    assert "image-build" not in result.stdout
    assert "helm status osac" not in result.stdout


def test_helm_argument_builder_maps_services_and_image_references() -> None:
    debug_args = build_helm_args(
        services_value="osac-operator,fulfillment-service",
        debug_value="true",
        platform="openshift",
        debug_continue_value="false",
        fulfillment_debug_continue_value="true",
        osac_operator_image="quay.io:5000/team/operator:debug",
        fulfillment_service_image="quay.io/team/fulfillment:debug",
        rollout_token="2026-10-22T12:34:56.123456Z",
    )
    assert "operator.debug.enabled=true" in debug_args
    assert "operator.debug.image=quay.io:5000/team/operator:debug" in debug_args
    assert "service.debug.enabled=true" in debug_args
    assert "service.debug.image=quay.io/team/fulfillment:debug" in debug_args
    assert "operator.debug.fsGroup=auto" in debug_args
    assert (
        "operator.debug.rolloutToken=2026-10-22T12:34:56.123456Z" in debug_args
    )
    assert (
        "service.debug.rolloutToken=2026-10-22T12:34:56.123456Z" in debug_args
    )

    fulfillment_only_args = build_helm_args(
        services_value="fulfillment-service",
        debug_value="true",
        platform="openshift",
        debug_continue_value="true",
        fulfillment_debug_continue_value="false",
        fulfillment_service_image="quay.io/team/fulfillment:debug",
        rollout_token="2026-10-22T12:34:56.123456Z",
    )
    assert "service.debug.rolloutToken=2026-10-22T12:34:56.123456Z" in fulfillment_only_args
    assert not any("operator.debug.rolloutToken" in arg for arg in fulfillment_only_args)

    normal_args = build_helm_args(
        services_value="osac-operator,fulfillment-service",
        debug_value="false",
        platform="kind",
        debug_continue_value="true",
        fulfillment_debug_continue_value="false",
        osac_operator_image="quay.io:5000/team/operator:1.2.3",
        fulfillment_service_image="quay.io/team/fulfillment:1.2.3",
        rollout_token="2026-10-22T12:34:56.123456Z",
    )
    assert "operator.debug.enabled=false" in normal_args
    assert "operator.image.repository=quay.io:5000/team/operator" in normal_args
    assert "operator.image.tag=1.2.3" in normal_args
    assert "service.debug.enabled=false" in normal_args
    assert "service.debug.image=" in normal_args
    assert "service.images.service.repository=quay.io/team/fulfillment" in normal_args
    assert "service.images.service.tag=1.2.3" in normal_args
    assert (
        "operator.debug.rolloutToken=2026-10-22T12:34:56.123456Z" in normal_args
    )
    assert (
        "service.debug.rolloutToken=2026-10-22T12:34:56.123456Z" in normal_args
    )


def test_helm_argument_builder_validates_debug_image_requirements() -> None:
    try:
        build_helm_args(
            services_value="fulfillment-service",
            debug_value="true",
            platform="openshift",
            debug_continue_value="true",
            fulfillment_debug_continue_value="false",
        )
    except ConfigurationError as error:
        assert "FULFILLMENT_SERVICE_IMAGE is required" in str(error)
    else:
        raise AssertionError("debug image omission was accepted")


def test_csi_helm_argument_builder_maps_controller_and_node_continue() -> None:
    args = build_csi_helm_args(
        platform="openshift",
        namespace="osac-csi",
        debug_value="true",
        debug_continue_value="true",
        node_continue_value="false",
        image="quay.io/team/csi:debug",
        rollout_token="2026-10-28T12:34:56Z",
    )
    assert "debug.enabled=true" in args
    assert "debug.continue=true" in args
    assert "debug.nodeContinue=false" in args
    assert "debug.image=quay.io/team/csi:debug" in args
    assert "namespace=osac-csi" in args
    assert "image.pullPolicy=Always" in args
    assert "debug.fsGroup=auto" in args
    assert "debug.rolloutToken=2026-10-28T12:34:56Z" in args

    normal_args = build_csi_helm_args(
        platform="kind",
        namespace="osac-csi",
        debug_value="false",
        debug_continue_value="true",
        node_continue_value="true",
        image="quay.io:5000/team/csi:1.2.3",
    )
    assert "debug.enabled=false" in normal_args
    assert "image.repository=quay.io:5000/team/csi" in normal_args
    assert "image.tag=1.2.3" in normal_args
    assert "image.pullPolicy=IfNotPresent" in normal_args
    assert "debug.fsGroup=1001" in normal_args


def test_csi_helm_argument_builder_requires_debug_image() -> None:
    try:
        build_csi_helm_args(
            platform="kind",
            namespace="osac-csi",
            debug_value="true",
            debug_continue_value="true",
            node_continue_value="true",
        )
    except CSIDriverConfigurationError as error:
        assert "CSI_DRIVER_SERVICE_IMAGE is required" in str(error)
    else:
        raise AssertionError("CSI debug image omission was accepted")


def test_csi_helm_argument_builder_keeps_the_chart_namespace_fixed() -> None:
    try:
        build_csi_helm_args(
            platform="kind",
            namespace="custom-csi",
            debug_value="false",
            debug_continue_value="true",
            node_continue_value="true",
        )
    except CSIDriverConfigurationError as error:
        assert "must be osac-csi" in str(error)
    else:
        raise AssertionError("CSI chart accepted a non-hub namespace")


def test_enable_debug_defaults_to_osac_and_accepts_namespace_override() -> None:
    output = dry_run("enable-debug")
    assert "/apis/security.openshift.io/v1" in output
    assert (
        'helm upgrade --install "osac-debug-scc" "charts/osac-debug-scc"'
        in output
    )
    assert '--namespace "kube-system"' in output
    assert '--set-string targetNamespace="osac"' in output
    assert '--set-string csiDriverNamespace="osac-csi"' in output
    assert "PLATFORM is required" not in output
    assert "PROFILE is required" not in output

    override = dry_run("enable-debug", "NS=osac-dev")
    assert '--set-string targetNamespace="osac-dev"' in override
    csi_override = dry_run("enable-debug", "CSI_DRIVER_NAMESPACE=osac-csi-test")
    assert '--set-string csiDriverNamespace="osac-csi-test"' in csi_override


def test_disable_debug_checks_pods_before_uninstall() -> None:
    output = dry_run("disable-debug")
    assert "kubectl get pods --all-namespaces" in output
    assert "--field-selector=status.phase!=Succeeded,status.phase!=Failed" in output
    assert 'awk \'$3 == "osac-remote-debug" || $3 == "osac-operator-debug"' in output
    assert 'helm uninstall "osac-debug-scc"' in output
    assert output.index("kubectl get pods --all-namespaces") < output.index(
        'helm uninstall "osac-debug-scc"'
    )
    assert "PLATFORM is required" not in output
    assert "PROFILE is required" not in output
    assert "NS is required" not in output


def main() -> None:
    test_build_debug_operator_pushes_without_helm()
    test_build_debug_fulfillment_pushes_shared_image_without_helm()
    test_build_normal_operator_cross_platform_without_push()
    test_build_normal_fulfillment_cross_platform_without_push()
    test_build_debug_csi_pushes_image_without_helm()
    test_push_requires_an_explicit_image_reference()
    test_install_services_upgrades_once_with_both_debug_images()
    test_install_services_openshift_checks_scc_and_sets_cluster_hosts()
    test_install_services_can_apply_normal_cross_built_images()
    test_install_services_builds_only_selected_service()
    test_install_services_delegates_csi_to_standalone_release_only()
    test_install_csi_driver_builds_loads_and_upgrades_standalone_release()
    test_standalone_csi_build_requires_an_image_when_pushing()
    test_install_csi_driver_defaults_to_installed_release_and_namespace()
    test_install_services_build_false_skips_build_goals()
    test_continue_shorthand_sets_both_runtime_modes()
    test_continue_defaults_to_true_for_both_services()
    test_component_continue_override_does_not_change_other_service()
    test_install_services_resolves_config_and_cli_image_overrides()
    test_image_config_selects_debug_or_normal_and_cli_wins()
    test_csi_image_config_selects_debug_or_normal_and_cli_wins()
    test_local_builds_are_rejected_for_openshift()
    test_kind_local_build_uses_and_loads_default_tags()
    test_install_services_requires_images_for_default_push_builds()
    test_install_services_supports_dev_full_overlays()
    test_install_services_rejects_unknown_services_before_cluster_calls()
    test_helm_argument_builder_maps_services_and_image_references()
    test_helm_argument_builder_validates_debug_image_requirements()
    test_csi_helm_argument_builder_maps_controller_and_node_continue()
    test_csi_helm_argument_builder_requires_debug_image()
    test_csi_helm_argument_builder_keeps_the_chart_namespace_fixed()
    test_enable_debug_defaults_to_osac_and_accepts_namespace_override()
    test_disable_debug_checks_pods_before_uninstall()
    print("remote-debug Make contracts passed")


if __name__ == "__main__":
    main()
