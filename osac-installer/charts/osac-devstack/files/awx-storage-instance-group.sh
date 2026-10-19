#!/usr/bin/env bash
# Configure the isolated AWX container group used by storage-provider jobs.

osac_awx_api_request() {
  local method="$1" api="$2" token="$3" endpoint="$4" payload="${5:-}"
  local -a args=(--silent --show-error --fail -X "${method}" \
    -H "Authorization: Bearer ${token}" -H "Content-Type: application/json")
  [[ -z "${payload}" ]] || args+=(--data "${payload}")
  curl "${args[@]}" "${api}${endpoint}"
}

osac_ensure_storage_instance_group() {
  local api="$1" token="$2" credential_id="$3" namespace="$4"
  local ee_image="$5" ee_pull="$6" group_name="osac-storage-operations-ig"
  local response group_id pod_spec payload

  response=$(osac_awx_api_request GET "${api}" "${token}" \
    "/instance_groups/?name=${group_name}")
  group_id=$(python3 -c '
import json
import sys

name = sys.argv[1]
groups = json.load(sys.stdin).get("results", [])
matches = [str(group["id"]) for group in groups if group.get("name") == name]
if len(matches) > 1:
    raise SystemExit(f"multiple AWX instance groups named {name!r}")
print(matches[0] if matches else "")
' "${group_name}" <<<"${response}")

  pod_spec=$(python3 -c '
import json
import sys

namespace, image, pull = sys.argv[1:]
image_pull_policy = {
    "always": "Always",
    "missing": "IfNotPresent",
    "never": "Never",
}.get(pull.lower(), "Always")
print(json.dumps({
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {
        "namespace": namespace,
        "labels": {"ansible_job": ""},
    },
    "spec": {
        "serviceAccountName": "awx-runner",
        "containers": [{
            "name": "worker",
            "image": image,
            "imagePullPolicy": image_pull_policy,
            "args": ["ansible-runner", "worker", "--private-data-dir=/runner"],
            "envFrom": [
                {"secretRef": {"name": "storage-operations-ig", "optional": True}},
                {"configMapRef": {"name": "storage-operations-ig", "optional": True}},
            ],
        }],
    },
}))
' "${namespace}" "${ee_image}" "${ee_pull}")
  payload=$(python3 -c '
import json
import sys

name, credential, pod_spec = sys.argv[1:]
print(json.dumps({
    "name": name,
    "credential": int(credential),
    "is_container_group": True,
    "pod_spec_override": pod_spec,
}))
' "${group_name}" "${credential_id}" "${pod_spec}")

  if [[ -n "${group_id}" ]]; then
    response=$(osac_awx_api_request PATCH "${api}" "${token}" \
      "/instance_groups/${group_id}/" "${payload}")
  else
    response=$(osac_awx_api_request POST "${api}" "${token}" \
      "/instance_groups/" "${payload}")
    group_id=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("id", ""))' <<<"${response}")
  fi
  [[ -n "${group_id}" ]] || return 1
  printf '%s' "${group_id}"
}

osac_assign_storage_instance_group() {
  local api="$1" token="$2" template_name="$3" group_id="$4"
  local response template_id associations current_ids existing_id

  response=$(osac_awx_api_request GET "${api}" "${token}" \
    "/job_templates/?name=${template_name}")
  template_id=$(python3 -c '
import json
import sys

name = sys.argv[1]
templates = json.load(sys.stdin).get("results", [])
matches = [
    str(template["id"])
    for template in templates
    if template.get("name") == name and template.get("organization") == 1
]
if len(matches) > 1:
    raise SystemExit(f"multiple AWX job templates named {name!r}")
print(matches[0] if matches else "")
' "${template_name}" <<<"${response}")
  [[ -n "${template_id}" ]] || {
    echo "AWX storage job template ${template_name} was not found" >&2
    return 1
  }

  associations=$(osac_awx_api_request GET "${api}" "${token}" \
    "/job_templates/${template_id}/instance_groups/")
  current_ids=$(python3 -c '
import json
import sys

print(" ".join(str(group["id"]) for group in json.load(sys.stdin).get("results", [])))
' <<<"${associations}")
  for existing_id in ${current_ids}; do
    if [[ "${existing_id}" != "${group_id}" ]]; then
      osac_awx_api_request POST "${api}" "${token}" \
        "/job_templates/${template_id}/instance_groups/" \
        "$(python3 -c 'import json,sys; print(json.dumps({"id": int(sys.argv[1]), "disassociate": True}))' "${existing_id}")" \
        >/dev/null
    fi
  done
  if [[ ! " ${current_ids} " =~ " ${group_id} " ]]; then
    osac_awx_api_request POST "${api}" "${token}" \
      "/job_templates/${template_id}/instance_groups/" \
      "$(python3 -c 'import json,sys; print(json.dumps({"id": int(sys.argv[1])}))' "${group_id}")" \
      >/dev/null
  fi
  osac_awx_api_request PATCH "${api}" "${token}" \
    "/job_templates/${template_id}/" \
    '{"prevent_instance_group_fallback": true}' >/dev/null
}
