#!/usr/bin/env python3
"""Render contracts for the CSI driver's remote-debug configuration."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/csi-driver"
ROLLOUT_TOKEN = "2026-10-28T12:34:56Z"
DEBUG_IMAGE = "registry.example.test/osac-csi-driver:debug"


def render(*overrides: str, openshift: bool = False) -> list[dict]:
    command = [
        os.environ.get("HELM_BIN", "helm"),
        "template",
        "csi-driver",
        str(CHART),
        "--namespace",
        "osac-csi",
        *overrides,
    ]
    if openshift:
        command += ["--api-versions", "security.openshift.io/v1"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"Helm render failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def workload(docs: list[dict], kind: str, component: str) -> dict:
    for item in docs:
        if (
            item.get("kind") == kind
            and item.get("metadata", {}).get("name") == f"csi-driver-{component}"
        ):
            return item
    raise AssertionError(f"{kind} for {component} was not rendered")


def driver_container(item: dict) -> dict:
    containers = item["spec"]["template"]["spec"]["containers"]
    return next(container for container in containers if container["name"] == "osac-csi-driver")


def test_normal_render_is_unchanged() -> None:
    docs = render()
    for kind, component in (("Deployment", "controller"), ("DaemonSet", "node")):
        item = workload(docs, kind, component)
        pod_spec = item["spec"]["template"]["spec"]
        container = driver_container(item)
        assert pod_spec["serviceAccountName"] == f"csi-driver-{component}"
        assert container["image"] == "ghcr.io/osac-project/osac-csi-driver:main"
        assert "command" not in container
        assert "ports" not in container
        assert "OSAC_DEBUG_CONTINUE" not in {
            variable["name"] for variable in container.get("env", [])
        }

    openshift_docs = render(openshift=True)
    node_scc_binding = next(
        item
        for item in openshift_docs
        if item.get("kind") == "RoleBinding"
        and item["metadata"]["name"] == "csi-driver-node-scc-use"
    )
    assert node_scc_binding["subjects"][0]["name"] == "csi-driver-node"


def test_debug_render_wraps_controller_and_node_with_independent_continue() -> None:
    docs = render(
        "--set",
        "debug.enabled=true",
        "--set-string",
        f"debug.image={DEBUG_IMAGE}",
        "--set",
        "debug.continue=true",
        "--set",
        "debug.nodeContinue=false",
        "--set-string",
        f"debug.rolloutToken={ROLLOUT_TOKEN}",
        openshift=True,
    )

    for kind, component, continue_value in (
        ("Deployment", "controller", "true"),
        ("DaemonSet", "node", "false"),
    ):
        item = workload(docs, kind, component)
        pod_spec = item["spec"]["template"]["spec"]
        container = driver_container(item)
        assert pod_spec["serviceAccountName"] == f"csi-driver-{component}-debug"
        assert container["image"] == DEBUG_IMAGE
        assert container["command"] == ["/usr/local/bin/debug-entrypoint"]
        assert container["ports"] == [
            {"name": "debugger", "containerPort": 2345, "protocol": "TCP"}
        ]
        assert item["spec"]["template"]["metadata"]["annotations"][
            "kubectl.kubernetes.io/restartedAt"
        ] == ROLLOUT_TOKEN
        env = {
            variable["name"]: variable.get("value")
            for variable in container["env"]
        }
        assert env["OSAC_DEBUG_CONTINUE"] == continue_value
        if component == "controller":
            assert pod_spec["securityContext"]["seccompProfile"]["type"] == "Unconfined"
            assert container["securityContext"]["capabilities"]["add"] == ["SYS_PTRACE"]
        else:
            assert "securityContext" not in pod_spec
            assert container["securityContext"]["privileged"] is True

    service_accounts = {
        item["metadata"]["name"]: item
        for item in docs
        if item.get("kind") == "ServiceAccount"
    }
    for account in ("csi-driver-controller-debug", "csi-driver-node-debug"):
        assert service_accounts[account]["automountServiceAccountToken"] is True

    controller_binding = next(
        item
        for item in docs
        if item.get("kind") == "ClusterRoleBinding"
        and item["metadata"]["name"] == "csi-driver-provisioner"
    )
    assert controller_binding["subjects"][0]["name"] == "csi-driver-controller-debug"
    node_binding = next(
        item
        for item in docs
        if item.get("kind") == "ClusterRoleBinding"
        and item["metadata"]["name"] == "csi-driver-node"
    )
    assert node_binding["subjects"][0]["name"] == "csi-driver-node-debug"

    node_scc_role = next(
        item
        for item in docs
        if item.get("kind") == "ClusterRole"
        and item["metadata"]["name"] == "csi-driver-node-scc-use"
    )
    assert node_scc_role["rules"] == [
        {
            "apiGroups": ["security.openshift.io"],
            "resources": ["securitycontextconstraints"],
            "resourceNames": ["csi-driver-node"],
            "verbs": ["use"],
        }
    ]
    node_scc_binding = next(
        item
        for item in docs
        if item.get("kind") == "RoleBinding"
        and item["metadata"]["name"] == "csi-driver-node-scc-use"
    )
    assert node_scc_binding["roleRef"] == {
        "apiGroup": "rbac.authorization.k8s.io",
        "kind": "ClusterRole",
        "name": "csi-driver-node-scc-use",
    }
    assert node_scc_binding["subjects"] == [
        {
            "kind": "ServiceAccount",
            "name": "csi-driver-node-debug",
            "namespace": "osac-csi",
        }
    ]

    node_scc = next(item for item in docs if item.get("kind") == "SecurityContextConstraints")
    assert node_scc["users"] == ["system:serviceaccount:osac-csi:csi-driver-node-debug"]
    assert not any(
        port.get("port") == 2345
        for item in docs
        if item.get("kind") == "Service"
        for port in item.get("spec", {}).get("ports", [])
    )


def main() -> None:
    test_normal_render_is_unchanged()
    test_debug_render_wraps_controller_and_node_with_independent_continue()
    print("CSI remote-debug render contracts passed")


if __name__ == "__main__":
    main()
