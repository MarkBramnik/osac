"""Tests for storage TLS validation defaults and task wiring."""

from pathlib import Path

import yaml
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar


AAP_ROOT = Path(__file__).parents[2]
ROLE_ROOT = (
    AAP_ROOT / "collections/ansible_collections/osac/templates/roles/vast_storage"
)


def resolve_certificate_validation(variables=None):
    """Render the storage role's shared TLS policy from the environment."""
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yaml").read_text())
    templar = Templar(loader=DataLoader(), variables=variables or {})
    return templar.template(defaults["enable_certificate_validation"])


def resolve_storage_role_validation(variables=None):
    """Render the role alias consumed by its VAST HTTP clients."""
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yaml").read_text())
    template_variables = dict(variables or {})
    template_variables.setdefault(
        "enable_certificate_validation",
        resolve_certificate_validation(template_variables),
    )
    templar = Templar(
        loader=DataLoader(),
        variables=template_variables,
    )
    return templar.template(defaults["vast_storage_validate_certs"])


def iter_validation_values(node):
    """Yield every nested ``validate_certs`` value in an Ansible task tree."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "validate_certs":
                yield value
            yield from iter_validation_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_validation_values(value)


def test_certificate_validation_defaults_to_enabled(monkeypatch):
    """Keep TLS verification enabled when neither environment setting is present."""
    monkeypatch.delenv("ENABLE_CERTIFICATE_VALIDATION", raising=False)
    monkeypatch.delenv("VAST_VALIDATE_CERTS", raising=False)

    assert resolve_certificate_validation() is True
    assert resolve_storage_role_validation() is True


def test_certificate_validation_uses_runtime_environment(monkeypatch):
    """Allow the storage worker environment to disable TLS verification."""
    monkeypatch.setenv("ENABLE_CERTIFICATE_VALIDATION", "false")

    assert resolve_certificate_validation() is False
    assert resolve_storage_role_validation() is False


def test_storage_provider_scoped_override_precedes_worker_environment(monkeypatch):
    """Let JIT storage policy override the compute worker's environment."""
    monkeypatch.setenv("ENABLE_CERTIFICATE_VALIDATION", "true")
    variables = {"vast_storage_certificate_validation_override": False}

    assert resolve_certificate_validation(variables) is False
    assert resolve_storage_role_validation(variables) is False


def test_unrecognized_certificate_validation_value_keeps_secure_default(monkeypatch):
    """Treat unrecognized setting values as enabled to preserve the secure default."""
    monkeypatch.setenv("ENABLE_CERTIFICATE_VALIDATION", "unexpected")

    assert resolve_certificate_validation() is True
    assert resolve_storage_role_validation() is True


def test_legacy_vast_setting_is_a_fallback(monkeypatch):
    """Preserve the legacy override for manually managed storage groups."""
    monkeypatch.delenv("ENABLE_CERTIFICATE_VALIDATION", raising=False)
    monkeypatch.setenv("VAST_VALIDATE_CERTS", "false")

    assert resolve_certificate_validation() is False


def test_storage_role_alias_uses_the_shared_setting():
    """Keep the VAST-specific role variable aliased to the shared TLS policy."""
    defaults = yaml.safe_load((ROLE_ROOT / "defaults/main.yaml").read_text())

    assert defaults["vast_storage_validate_certs"] == "{{ enable_certificate_validation }}"


def test_compute_jit_passes_the_storage_provider_tls_override():
    """Keep the AWX compute JIT policy scoped to its storage-provider call."""
    playbook = yaml.safe_load(
        (AAP_ROOT / "playbook_osac_create_compute_instance.yml").read_text()
    )
    jit_tasks = [
        task
        for play in playbook
        for task in play.get("pre_tasks", [])
        if task.get("name") == "Ensure storage is provisioned for tenant (JIT)"
    ]

    assert len(jit_tasks) == 1
    assert jit_tasks[0]["vars"]["storage_provider_certificate_validation"] == (
        "{{ osac_storage_provider_certificate_validation | default(omit) }}"
    )


def test_storage_http_clients_use_the_shared_validation_setting():
    """Ensure every storage task wires HTTP certificate checks to one policy."""
    tasks_dir = ROLE_ROOT / "tasks"
    paths = sorted((*tasks_dir.glob("*.yaml"), *tasks_dir.glob("*.yml")))
    validation_values = []
    for path in paths:
        validation_values.extend(
            (path, value)
            for value in iter_validation_values(yaml.safe_load(path.read_text()))
        )

    assert validation_values, "expected storage task TLS options"
    for path, value in validation_values:
        assert value in {
            "{{ vast_storage_validate_certs }}",
            "{{ _vast_vms_conn.validate_certs }}",
        }, path
