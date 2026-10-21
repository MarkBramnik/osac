#!/usr/bin/env python3
"""Render contracts for the operator's optional remote-debug profile."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/operator"


def render(*values: str) -> list[dict]:
    command = [
        os.environ.get("HELM_BIN", "helm"),
        "template",
        "remote-debug-test",
        str(CHART),
        "--namespace",
        "remote-debug-test",
        *values,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError(f"Helm render failed:\n{result.stderr}")
    return [doc for doc in yaml.safe_load_all(result.stdout) if isinstance(doc, dict)]


def deployment_for_container(docs: list[dict], container_name: str) -> tuple[dict, dict]:
    for document in docs:
        if document.get("kind") != "Deployment":
            continue
        containers = document["spec"]["template"]["spec"]["containers"]
        for container in containers:
            if container["name"] == container_name:
                return document, container
    raise AssertionError(f"Deployment container {container_name!r} was not rendered")


def test_debug_off_is_unchanged(docs: list[dict]) -> None:
    manager_deployment, manager = deployment_for_container(docs, "manager")
    pod_security = manager_deployment["spec"]["template"]["spec"]["securityContext"]
    assert pod_security["seccompProfile"]["type"] == "RuntimeDefault"
    assert manager_deployment["spec"]["template"]["spec"]["serviceAccountName"] == "remote-debug-test-osac-operator"
    assert manager["command"] == ["/usr/local/bin/manager"]
    assert manager["image"] == "ghcr.io/osac-project/osac-operator:latest"
    assert "--leader-elect=true" in manager["args"]
    assert "livenessProbe" in manager
    assert "readinessProbe" in manager
    assert not any(
        capability == "SYS_PTRACE"
        for capability in manager["securityContext"]["capabilities"].get("add", [])
    )
    assert not any(mount["mountPath"] == "/tmp" for mount in manager["volumeMounts"])

    _, proxy = deployment_for_container(docs, "console-proxy")
    assert proxy["image"] == "ghcr.io/osac-project/osac-operator:latest"
    assert "livenessProbe" in proxy
    assert "readinessProbe" in proxy


def test_debug_on_is_manager_only(docs: list[dict], continue_value: str) -> None:
    manager_deployment, manager = deployment_for_container(docs, "manager")
    pod_spec = manager_deployment["spec"]["template"]["spec"]
    pod_security = pod_spec["securityContext"]
    assert pod_security["seccompProfile"]["type"] == "Unconfined"
    assert pod_security["fsGroup"] == 1001
    assert pod_spec["serviceAccountName"] == "remote-debug-test-osac-operator-debug"
    assert manager["command"] == ["/usr/local/bin/debug-entrypoint"]
    assert manager["image"] == "registry.example.test/osac-operator:dev-debug"
    assert "--leader-elect=false" in manager["args"]
    assert manager["imagePullPolicy"] == "IfNotPresent"
    assert manager["ports"] == [
        {"name": "debugger", "containerPort": 2345, "protocol": "TCP"}
    ]
    assert "livenessProbe" not in manager
    assert "readinessProbe" not in manager
    assert manager["securityContext"]["capabilities"]["add"] == ["SYS_PTRACE"]
    assert manager["securityContext"]["capabilities"]["drop"] == ["ALL"]
    assert {"name": "debug-tmp", "mountPath": "/tmp"} in manager["volumeMounts"]
    assert any(
        volume["name"] == "debug-tmp" and "emptyDir" in volume
        for volume in manager_deployment["spec"]["template"]["spec"]["volumes"]
    )
    assert manager["resources"]["limits"] == {"cpu": "1", "memory": "1Gi"}
    env = {item["name"]: item.get("value") for item in manager["env"]}
    assert env["OSAC_DEBUG_CONTINUE"] == continue_value
    assert env["HOME"] == "/tmp"
    assert env["TMPDIR"] == "/tmp"

    _, proxy = deployment_for_container(docs, "console-proxy")
    assert proxy["image"] == "ghcr.io/osac-project/osac-operator:latest"
    assert "livenessProbe" in proxy
    assert "readinessProbe" in proxy
    assert "SYS_PTRACE" not in proxy["securityContext"]["capabilities"].get("add", [])

    debugger_services = [
        service
        for service in docs
        if service.get("kind") == "Service"
        and any(
            port.get("port") == 2345
            for port in service.get("spec", {}).get("ports", [])
        )
    ]
    assert not debugger_services, "Delve must not be exposed by a Service"


def main() -> None:
    test_debug_off_is_unchanged(render())
    test_debug_on_is_manager_only(
        render(
            "--set",
            "debug.enabled=true",
            "--set",
            "debug.continue=false",
            "--set",
            "debug.fsGroup=1001",
            "--set-string",
            "debug.image=registry.example.test/osac-operator:dev-debug",
        ),
        "false",
    )
    test_debug_on_is_manager_only(
        render(
            "--set",
            "debug.enabled=true",
            "--set",
            "debug.continue=true",
            "--set",
            "debug.fsGroup=1001",
            "--set-string",
            "debug.image=registry.example.test/osac-operator:dev-debug",
        ),
        "true",
    )
    print("remote-debug Helm render contracts passed")


if __name__ == "__main__":
    main()
