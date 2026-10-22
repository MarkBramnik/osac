#!/usr/bin/env python3
"""Verify focused operator and Fulfillment debug chart renders."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml
from remote_debug_helm_args import build_helm_args

INSTALLER = Path(__file__).resolve().parents[1]
CHART = INSTALLER / "charts/osac"
DEFAULT_VALUES = CHART / "ci/default-values.yaml"
FULFILLMENT_CHART_VALUES = (
    INSTALLER.parent / "fulfillment-service/charts/service/values.yaml"
)
DEBUG_IMAGE = "registry.example.test/osac-operator:dev-debug"
FULFILLMENT_DEBUG_IMAGE = "registry.example.test/fulfillment-service:dev-debug"
ROLLOUT_TOKEN = "2026-10-22T12:34:56.123456Z"


def render(*overrides: str) -> list[dict]:
    command = [
        os.environ.get("HELM_BIN", "helm"),
        "template",
        "osac",
        str(CHART),
        "--namespace",
        "debug-test",
        "--values",
        str(DEFAULT_VALUES),
    ]
    command.extend(overrides)
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"Helm render failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def render_debug_scc() -> list[dict]:
    command = [
        os.environ.get("HELM_BIN", "helm"),
        "template",
        "osac-debug-scc",
        str(INSTALLER / "charts/osac-debug-scc"),
        "--namespace",
        "kube-system",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"Debug SCC chart render failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def operator_manager(docs: list[dict]) -> tuple[dict, dict]:
    for document in docs:
        if document.get("kind") != "Deployment":
            continue
        app_name = document.get("metadata", {}).get("labels", {}).get(
            "app.kubernetes.io/name"
        )
        if app_name != "operator":
            continue
        pod_spec = document["spec"]["template"]["spec"]
        for container in pod_spec["containers"]:
            if container["name"] == "manager":
                return document, container
    raise AssertionError("operator manager Deployment was not rendered")


def console_proxy(docs: list[dict]) -> dict:
    for document in docs:
        if document.get("kind") != "Deployment":
            continue
        for container in document["spec"]["template"]["spec"]["containers"]:
            if container["name"] == "console-proxy":
                return container
    raise AssertionError("console-proxy Deployment was not rendered")


def deployment_container(
    docs: list[dict], deployment_name: str, container_name: str
) -> tuple[dict, dict]:
    for document in docs:
        if (
            document.get("kind") == "Deployment"
            and document.get("metadata", {}).get("name") == deployment_name
        ):
            pod_spec = document["spec"]["template"]["spec"]
            for container in pod_spec["containers"]:
                if container["name"] == container_name:
                    return document, container
    raise AssertionError(
        f"{deployment_name}/{container_name} Deployment container was not rendered"
    )


def test_debug_image_overrides_are_not_wizard_controls() -> None:
    schema = json.loads((CHART / "values.schema.json").read_text(encoding="utf-8"))
    operator_properties = schema["properties"]["operator"]["properties"]
    assert "debug" not in operator_properties
    operator_schema = schema["properties"]["operator"]
    operator_debug_schema = operator_schema["patternProperties"]["^debug$"]
    assert operator_debug_schema["properties"]["enabled"]["default"] is False
    assert operator_debug_schema["properties"]["continue"]["default"] is True
    assert "image" not in operator_debug_schema["required"]
    assert operator_debug_schema["properties"]["rolloutToken"]["type"] == "string"
    values = yaml.safe_load((CHART / "values.yaml").read_text(encoding="utf-8"))
    assert "debug" not in values["operator"]

    service_schema = schema["properties"]["service"]
    service_properties = service_schema["properties"]
    assert "debug" not in service_properties
    debug_schema = service_schema["patternProperties"]["^debug$"]
    assert debug_schema["properties"]["enabled"]["default"] is False
    assert debug_schema["properties"]["continue"]["default"] is True
    assert debug_schema["properties"]["rolloutToken"]["type"] == "string"
    assert "debug" not in values["service"]
    fulfillment_values = yaml.safe_load(
        FULFILLMENT_CHART_VALUES.read_text(encoding="utf-8")
    )
    assert fulfillment_values["debug"]["continue"] is True


def test_normal_umbrella_render_uses_normal_image() -> None:
    docs = render()
    manager_deployment, manager = operator_manager(docs)
    assert manager["command"] == ["/usr/local/bin/manager"]
    assert manager["image"] == "ghcr.io/osac-project/osac-operator:latest"
    assert "--leader-elect=true" in manager["args"]
    assert manager_deployment["spec"]["template"]["spec"]["serviceAccountName"] == "osac-operator"
    assert "livenessProbe" in manager
    assert "readinessProbe" in manager


def test_debug_upgrade_only_changes_operator_manager() -> None:
    docs = render(
        "--set",
        "operator.debug.enabled=true",
        "--set",
        "operator.debug.continue=false",
        "--set",
        "operator.debug.fsGroup=auto",
        "--set",
        "operator.debug.imagePullPolicy=Always",
        "--set-string",
        f"operator.debug.image={DEBUG_IMAGE}",
        "--set-string",
        f"operator.debug.rolloutToken={ROLLOUT_TOKEN}",
    )
    manager_deployment, manager = operator_manager(docs)
    pod_spec = manager_deployment["spec"]["template"]["spec"]
    assert pod_spec["serviceAccountName"] == "osac-operator-debug"
    assert manager["command"] == ["/usr/local/bin/debug-entrypoint"]
    assert manager["image"] == DEBUG_IMAGE
    assert "--leader-elect=false" in manager["args"]
    assert manager["imagePullPolicy"] == "Always"
    assert manager["ports"] == [
        {"name": "debugger", "containerPort": 2345, "protocol": "TCP"}
    ]
    assert "livenessProbe" not in manager
    assert "readinessProbe" not in manager
    assert manager["securityContext"]["capabilities"]["add"] == ["SYS_PTRACE"]
    assert pod_spec["securityContext"]["seccompProfile"]["type"] == "Unconfined"
    assert "fsGroup" not in pod_spec["securityContext"]
    assert {"name": "debug-tmp", "mountPath": "/tmp"} in manager["volumeMounts"]
    env = {item["name"]: item.get("value") for item in manager["env"]}
    assert env["OSAC_DEBUG_CONTINUE"] == "false"
    assert "OSAC_DEBUG_LISTEN_ADDRESS" not in env
    assert manager_deployment["spec"]["template"]["metadata"]["annotations"][
        "kubectl.kubernetes.io/restartedAt"
    ] == ROLLOUT_TOKEN

    proxy = console_proxy(docs)
    assert proxy["image"] == "ghcr.io/osac-project/osac-operator:latest"
    assert "livenessProbe" in proxy
    assert "readinessProbe" in proxy
    assert "SYS_PTRACE" not in proxy["securityContext"]["capabilities"].get("add", [])


def test_combined_services_debug_render_validates_without_skipping_schema() -> None:
    helm_args = build_helm_args(
        services_value="osac-operator,fulfillment-service",
        debug_value="true",
        platform="openshift",
        debug_continue_value="false",
        fulfillment_debug_continue_value="false",
        osac_operator_image=DEBUG_IMAGE,
        fulfillment_service_image=FULFILLMENT_DEBUG_IMAGE,
        rollout_token=ROLLOUT_TOKEN,
    )
    docs = render(*helm_args)
    manager_deployment, manager = operator_manager(docs)
    assert manager["image"] == DEBUG_IMAGE
    assert "livenessProbe" not in manager
    assert manager_deployment["spec"]["template"]["metadata"]["annotations"][
        "kubectl.kubernetes.io/restartedAt"
    ] == ROLLOUT_TOKEN
    controller_deployment, controller = deployment_container(
        docs, "fulfillment-controller", "controller"
    )
    assert controller["image"] == FULFILLMENT_DEBUG_IMAGE
    assert controller["command"] == ["/usr/local/bin/debug-entrypoint"]
    assert "livenessProbe" not in controller
    assert controller_deployment["spec"]["template"]["metadata"]["annotations"][
        "kubectl.kubernetes.io/restartedAt"
    ] == ROLLOUT_TOKEN


def test_selected_normal_images_render_with_debug_disabled() -> None:
    helm_args = build_helm_args(
        services_value="osac-operator,fulfillment-service",
        debug_value="false",
        platform="kind",
        debug_continue_value="true",
        fulfillment_debug_continue_value="false",
        osac_operator_image="registry.example.test/osac-operator:1.2.3",
        fulfillment_service_image="registry.example.test/fulfillment-service:1.2.3",
        rollout_token=ROLLOUT_TOKEN,
    )
    docs = render(*helm_args)
    manager_deployment, manager = operator_manager(docs)
    assert manager["image"] == "registry.example.test/osac-operator:1.2.3"
    assert manager["command"] == ["/usr/local/bin/manager"]
    assert "livenessProbe" in manager
    assert manager_deployment["spec"]["template"]["metadata"]["annotations"][
        "kubectl.kubernetes.io/restartedAt"
    ] == ROLLOUT_TOKEN
    controller_deployment, controller = deployment_container(
        docs, "fulfillment-controller", "controller"
    )
    assert controller["image"] == "registry.example.test/fulfillment-service:1.2.3"
    assert controller["command"] == ["/usr/local/bin/fulfillment-service"]
    assert "livenessProbe" in controller
    assert controller_deployment["spec"]["template"]["metadata"]["annotations"][
        "kubectl.kubernetes.io/restartedAt"
    ] == ROLLOUT_TOKEN


def test_normal_fulfillment_deployments_use_normal_image() -> None:
    docs = render()
    expected = {
        "fulfillment-controller": "controller",
        "fulfillment-grpc-server": "grpc-server",
        "fulfillment-rest-gateway": "rest-gateway",
        "fulfillment-event-publisher": "event-publisher",
    }
    for deployment_name, container_name in expected.items():
        deployment, container = deployment_container(
            docs, deployment_name, container_name
        )
        assert "kubectl.kubernetes.io/restartedAt" not in deployment[
            "spec"
        ]["template"]["metadata"].get("annotations", {})
        assert container["image"] == "ghcr.io/osac-project/fulfillment-service:latest"
        assert container["command"] == ["/usr/local/bin/fulfillment-service"]
        assert "debug" not in [port["name"] for port in container.get("ports", [])]
        assert "livenessProbe" in container
        assert "readinessProbe" in container
        if deployment_name != "fulfillment-event-publisher":
            strategy = deployment["spec"]["strategy"]
            assert strategy["type"] == "RollingUpdate"
            assert strategy["rollingUpdate"] == {
                "maxSurge": "25%",
                "maxUnavailable": "25%",
            }


def test_fulfillment_debug_upgrade_changes_only_four_processes() -> None:
    helm_args = build_helm_args(
        services_value="fulfillment-service",
        debug_value="true",
        platform="kind",
        debug_continue_value="true",
        fulfillment_debug_continue_value="false",
        fulfillment_service_image=FULFILLMENT_DEBUG_IMAGE,
        rollout_token=ROLLOUT_TOKEN,
    )
    docs = render(*helm_args)
    operator_deployment, _ = operator_manager(docs)
    assert "kubectl.kubernetes.io/restartedAt" not in operator_deployment[
        "spec"
    ]["template"]["metadata"].get("annotations", {})
    expected = {
        "fulfillment-controller": ("controller", "controller"),
        "fulfillment-grpc-server": ("grpc-server", "grpc-server"),
        "fulfillment-rest-gateway": ("rest-gateway", "rest-gateway"),
        "fulfillment-event-publisher": ("event-publisher", "event-publisher"),
    }
    for deployment_name, (container_name, process_name) in expected.items():
        deployment, container = deployment_container(
            docs, deployment_name, container_name
        )
        assert deployment["spec"]["template"]["metadata"]["annotations"][
            "kubectl.kubernetes.io/restartedAt"
        ] == ROLLOUT_TOKEN
        pod_spec = deployment["spec"]["template"]["spec"]
        strategy = deployment["spec"]["strategy"]
        if deployment_name != "fulfillment-event-publisher":
            assert strategy["type"] == "RollingUpdate"
            assert strategy["rollingUpdate"] == {
                "maxSurge": 0,
                "maxUnavailable": "100%",
            }
        else:
            assert strategy["type"] == "Recreate"
        assert container["image"] == FULFILLMENT_DEBUG_IMAGE
        assert container["imagePullPolicy"] == "IfNotPresent"
        assert container["command"] == ["/usr/local/bin/debug-entrypoint"]
        assert container["args"][:2] == ["start", process_name]
        assert container["ports"][-1] == {
            "name": "debug",
            "containerPort": 2345,
            "protocol": "TCP",
        }
        assert all("hostPort" not in port for port in container["ports"])
        assert "livenessProbe" not in container
        assert "readinessProbe" not in container
        assert container["securityContext"]["capabilities"]["add"] == ["SYS_PTRACE"]
        assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
        assert pod_spec["securityContext"]["seccompProfile"]["type"] == "Unconfined"
        assert {"name": "debug-tmp", "emptyDir": {}} in pod_spec["volumes"]
        assert {"name": "debug-tmp", "mountPath": "/tmp"} in container[
            "volumeMounts"
        ]
        env = {item["name"]: item.get("value") for item in container.get("env", [])}
        assert env["HOME"] == "/tmp"
        assert env["TMPDIR"] == "/tmp"
        assert env["OSAC_DEBUG_CONTINUE"] == "false"
        assert "OSAC_DEBUG_LISTEN_ADDRESS" not in env

    mcp_deployment, mcp = deployment_container(
        docs, "fulfillment-mcp-server", "mcp-server"
    )
    assert mcp_deployment["spec"]["strategy"]["type"] == "RollingUpdate"
    assert "kubectl.kubernetes.io/restartedAt" not in mcp_deployment[
        "spec"
    ]["template"]["metadata"].get("annotations", {})
    assert mcp["image"] == "ghcr.io/osac-project/fulfillment-service:latest"
    assert mcp["command"][0] == "/usr/local/bin/fulfillment-service"
    assert "/usr/local/bin/debug-entrypoint" not in mcp["command"]
    assert "debug" not in [port["name"] for port in mcp.get("ports", [])]

    assert not any(
        document.get("kind") == "Service"
        and any(port.get("port") == 2345 for port in document.get("spec", {}).get("ports", []))
        for document in docs
    )


def test_fulfillment_debug_disabled_uses_normal_image_and_probes() -> None:
    helm_args = build_helm_args(
        services_value="",
        debug_value="false",
        platform="kind",
        debug_continue_value="true",
        fulfillment_debug_continue_value="false",
    )
    docs = render(*helm_args)
    _, grpc = deployment_container(docs, "fulfillment-grpc-server", "grpc-server")
    assert grpc["image"] == "ghcr.io/osac-project/fulfillment-service:latest"
    assert grpc["command"] == ["/usr/local/bin/fulfillment-service"]
    assert "livenessProbe" in grpc
    assert "readinessProbe" in grpc


def test_fulfillment_debug_requires_an_image_reference() -> None:
    try:
        render(
            "--set",
            "service.debug.enabled=true",
            "--set",
            "service.debug.continue=false",
            "--set",
            "service.debug.imagePullPolicy=IfNotPresent",
        )
    except AssertionError as error:
        assert "image" in str(error)
    else:
        raise AssertionError("Fulfillment debug rendered without a debug image")


def test_operator_debug_requires_an_image_reference() -> None:
    try:
        render(
            "--set",
            "operator.debug.enabled=true",
            "--set",
            "operator.debug.continue=false",
            "--set",
            "operator.debug.fsGroup=auto",
            "--set",
            "operator.debug.imagePullPolicy=Always",
        )
    except AssertionError as error:
        assert "image" in str(error)
    else:
        raise AssertionError("Operator debug rendered without a debug image")


def test_debug_scc_is_narrow_except_for_explicit_cluster_wide_use() -> None:
    docs = render_debug_scc()
    assert len(docs) == 1
    scc = docs[0]
    assert scc["apiVersion"] == "security.openshift.io/v1"
    assert scc["kind"] == "SecurityContextConstraints"
    assert scc["metadata"]["name"] == "osac-remote-debug"
    assert scc["groups"] == ["system:serviceaccounts"]
    assert scc["users"] == []
    assert scc["allowedCapabilities"] == ["SYS_PTRACE"]
    assert scc["requiredDropCapabilities"] == ["ALL"]
    assert scc["seccompProfiles"] == ["runtime/default", "unconfined"]
    assert scc["allowPrivilegedContainer"] is False
    assert scc["allowPrivilegeEscalation"] is False
    assert scc["allowHostDirVolumePlugin"] is False
    assert scc["allowHostIPC"] is False
    assert scc["allowHostNetwork"] is False
    assert scc["allowHostPID"] is False
    assert scc["allowHostPorts"] is False
    assert scc["readOnlyRootFilesystem"] is True
    assert scc["priority"] == 0


def main() -> None:
    test_debug_image_overrides_are_not_wizard_controls()
    test_normal_umbrella_render_uses_normal_image()
    test_debug_upgrade_only_changes_operator_manager()
    test_combined_services_debug_render_validates_without_skipping_schema()
    test_selected_normal_images_render_with_debug_disabled()
    test_normal_fulfillment_deployments_use_normal_image()
    test_fulfillment_debug_upgrade_changes_only_four_processes()
    test_fulfillment_debug_disabled_uses_normal_image_and_probes()
    test_fulfillment_debug_requires_an_image_reference()
    test_operator_debug_requires_an_image_reference()
    test_debug_scc_is_narrow_except_for_explicit_cluster_wide_use()
    print("installer remote-debug render contracts passed")


if __name__ == "__main__":
    main()
