#!/usr/bin/env python3
"""Dry-run contracts for focused operator debug installation."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess


INSTALLER = Path(__file__).resolve().parents[1]


def dry_run(target: str, *variables: str) -> str:
    command = [
        "make",
        "--no-print-directory",
        "-n",
        "-C",
        str(INSTALLER),
        target,
        *variables,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"Make dry-run failed:\n{result.stderr}")
    return result.stdout


def test_kind_debug_builds_and_loads_only_operator() -> None:
    output = dry_run(
        "install-osac-operator",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "REMOTE_DEBUG=true",
        "OPERATOR_VALUES_FILE=values/custom-osac.yaml",
        "EXTRA_HELM_ARGS=--set=metering.enabled=false",
    )
    assert "image-build-debug" in output
    assert "kind-runtime.sh load image-archive" in output
    assert "image-push-debug" not in output
    assert "localhost/osac-operator:latest-debug" in output
    assert "--set operator.debug.enabled=true" in output
    assert "--set operator.debug.continue=true" in output
    assert "--set operator.debug.fsGroup=1001" in output
    assert "--set operator.debug.imagePullPolicy=IfNotPresent" in output
    assert "-f values/dev/kind-instance.yaml" in output
    assert '-f "values/custom-osac.yaml"' in output
    assert "--set=metering.enabled=false" in output
    assert "--reuse-values" not in output
    assert "--no-hooks" in output
    assert "--skip-schema-validation" not in output
    assert "osac-debug-scc" not in output


def test_openshift_cross_builds_and_pushes_explicit_image() -> None:
    image = "registry.example.test/osac/osac-operator:dev-debug"
    output = dry_run(
        "install-osac-operator",
        "PLATFORM=openshift",
        "PROFILE=vmaas-ci",
        "NS=osac",
        "REMOTE_DEBUG=true",
        f"REMOTE_DEBUG_IMAGE={image}",
        "IMAGE_PLATFORM=linux/amd64",
        "FULFILLMENT_DOMAIN=apps.example.test",
        "DEBUG_CONTINUE=false",
    )
    assert "IMAGE_PLATFORM=\"linux/amd64\"" in output
    assert f'podman push "{image}"' in output or f'docker push "{image}"' in output
    assert "-f values/vmaas-ci/instance.yaml" in output
    assert "--set operator.debug.enabled=true" in output
    assert "--set operator.debug.continue=false" in output
    assert "--set operator.debug.fsGroup=auto" in output
    assert "--set operator.debug.imagePullPolicy=Always" in output
    assert f"--set-string operator.debug.image={image}" in output
    assert "run 'make enable-debug' first" in output
    assert 'kubectl get scc "osac-remote-debug"' in output
    assert "--reuse-values" not in output
    assert "--skip-schema-validation" not in output
    assert output.index("remote-debug SCC is not enabled") < output.index(
        "image-build-debug"
    )
    assert "global.osacDeploymentId=osac/osac" in output
    assert "service.externalHostname=fulfillment-api-osac.apps.example.test" in output
    assert "service.internalHostname=fulfillment-internal-api-osac.apps.example.test" in output
    assert "global.clusterDomain=apps.example.test" in output
    assert "ui.auth.oidcIssuerURL=https://keycloak-keycloak.apps.example.test/realms/osac" in output


def test_kind_operator_debug_includes_dev_full_values_and_overrides() -> None:
    output = dry_run(
        "install-osac-operator",
        "PLATFORM=kind",
        "PROFILE=dev-full",
        "NS=osac",
        "REMOTE_DEBUG=false",
    )
    assert "-f values/dev/kind-instance.yaml" in output
    assert "-f values/dev/kind-instance-devfull.yaml" in output
    assert "--set-string operator.aap.url=http://awx-service.osac.svc.cluster.local:80/api" in output
    helm_version = subprocess.run(
        ["helm", "version", "--short"], capture_output=True, text=True, check=True
    ).stdout
    if re.match(r"v?4\.", helm_version):
        assert "--force-conflicts" in output


def test_disable_debug_restores_normal_profile_without_building() -> None:
    output = dry_run(
        "install-osac-operator",
        "PLATFORM=kind",
        "PROFILE=dev",
        "NS=osac",
        "REMOTE_DEBUG=false",
    )
    assert 'if [ "false" = true ]; then' in output
    assert "kind-runtime.sh load image-archive" not in output
    assert "operator.debug.enabled=false" in output
    assert "-f values/dev/kind-instance.yaml" in output
    assert "--reuse-values" not in output
    assert "--no-hooks" in output
    assert "--skip-schema-validation" not in output


def test_enable_debug_needs_no_deployment_arguments() -> None:
    output = dry_run("enable-debug")
    assert "/apis/security.openshift.io/v1" in output
    assert (
        'helm upgrade --install "osac-debug-scc" "charts/osac-debug-scc"'
        in output
    )
    assert '--namespace "kube-system"' in output
    assert "PLATFORM is required" not in output
    assert "PROFILE is required" not in output
    assert "NS is required" not in output


def test_disable_debug_checks_pods_before_uninstall() -> None:
    output = dry_run("disable-debug")
    assert "kubectl get pods --all-namespaces" in output
    assert "--field-selector=status.phase!=Succeeded,status.phase!=Failed" in output
    assert 'awk \'$3 == "osac-remote-debug" || $3 == "osac-operator-debug"' in output
    assert "helm uninstall \"osac-debug-scc\"" in output
    assert output.index("kubectl get pods --all-namespaces") < output.index(
        "helm uninstall \"osac-debug-scc\""
    )
    assert "PLATFORM is required" not in output
    assert "PROFILE is required" not in output
    assert "NS is required" not in output


def main() -> None:
    test_kind_debug_builds_and_loads_only_operator()
    test_openshift_cross_builds_and_pushes_explicit_image()
    test_kind_operator_debug_includes_dev_full_values_and_overrides()
    test_disable_debug_restores_normal_profile_without_building()
    test_enable_debug_needs_no_deployment_arguments()
    test_disable_debug_checks_pods_before_uninstall()
    print("remote-debug Make contracts passed")


if __name__ == "__main__":
    main()
