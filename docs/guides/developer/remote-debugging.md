# Remote debugging the OSAC Operator

This guide describes the focused remote-debug workflow for the OSAC Operator.
It covers the Delve-enabled image, the OpenShift SCC lifecycle, the installer
Make targets, the `debug-me` helper, and how debug mode differs from a regular
operator deployment.

For a concise user-facing summary, see the
[remote-debugging feature overview](../../features/remote-debugging.md).

## 1. Goal

Remote debugging lets an IDE attach to the OSAC Operator manager process running
in a Kubernetes or OpenShift Pod. The workflow builds a separate debug image,
updates the operator in the existing `osac` Helm release, and forwards Delve's
port to the developer's workstation. It does not install a second operator
Deployment, change the console proxy, or create a Service or Route for Delve.

The normal operator image does not contain or run Delve. The debug image is
selected only when `REMOTE_DEBUG=true` is passed to the focused installer target.

## 2. Architecture and configuration

### Image and process flow

The Containerfile has separate normal and debug build stages:

1. `builder` builds the normal manager, console proxy, and trust-admission
   binaries.
2. `debug-builder` builds `manager-debug` with optimizations and inlining
   disabled, and builds the `dlv` executable.
3. The normal `release` image contains the regular binaries. The separate
   `debug` target adds `manager-debug`, `dlv`, and
   `osac-operator/hack/debug-entrypoint.sh`.

See the [operator Containerfile](../../../osac-operator/Containerfile) and
[debug entrypoint](../../../osac-operator/hack/debug-entrypoint.sh).

The debug Pod starts the entrypoint, which `exec`s Delve as PID 1. Delve listens
on `127.0.0.1:2345` inside the manager container and launches
`/usr/local/bin/manager-debug` as its debuggee. `DEBUG_CONTINUE=true` continues
the manager immediately; `DEBUG_CONTINUE=false` leaves it paused until the IDE
attaches. The console proxy remains on the normal image.

The access path is:

```text
IDE -> 127.0.0.1:<localPort> -> kubectl/oc port-forward
    -> Pod port 2345 -> Delve (PID 1) -> manager-debug
```

Delve's API is unauthenticated, so it is bound to loopback in the Pod and exposed
only through port-forwarding. Do not expose it with a Service or Route.

### Helm and installer schema

The developer-only operator `debug` object is accepted by a narrowly matched
`patternProperties` rule, not an ordinary `properties` entry, so it does not add
a Wizard control. The focused `install-osac-operator` target uses the selected
profile values, optional `OPERATOR_VALUES_FILE`, and `EXTRA_HELM_ARGS`; debug
image and runtime settings are passed as command-line overrides. Normal Helm
schema validation remains enabled, and the target does not reuse stored release
values. It suppresses hooks so unrelated AAP bootstrap and template-publishing
jobs aren't rerun. The full `install-osac` workflow retains its normal hook
behavior.

The separate SCC chart has no user-configurable values. It creates the
cluster-scoped SCC `osac-remote-debug` and is installed as Helm release
`osac-debug-scc` in the fixed release-storage namespace `kube-system`.

For `debug-me`, configuration is in `debug-config.yaml`, not the installer
schema. Each `targets` item identifies a workload and container. For example:

```yaml
targets:
  - namespace: osac
    workload:
      kind: Deployment
      name: osac-operator
    container: manager
    debugPort: 2345
    localPort: 2345
    pid: 1
    delveVersion: v1.27.2
    substitutePath:
      - from: "${workspaceFolder}/osac-operator"
        to: "/opt/app-root/src/osac-operator"
```

`workload.name` is the Deployment name; the argument to `debug.sh start` is a
Pod name. The helper resolves the Pod's owner chain to match the configured
workload. `substitutePath` is optional and is printed into the VS Code attach
configuration. `from` is the local source path relative to the VS Code
workspace; `to` is the source path recorded in the Go binary. The example
assumes the monorepo root is the VS Code workspace.

`namespace` is optional; `container` is required. `debugPort` is the fallback
remote port for helper-managed attach; a prestarted Delve listener's port takes
precedence. `localPort` overrides the workstation port. `pid` selects the Go
process for helper-managed attach and is ignored for a prestarted server.
`delveVersion` selects the checksum-verified binary used by helper-managed
attach.

## 3. Make targets and flags

Run installer targets from the checkout root with `-C osac-installer`, or from
`osac-installer/` directly.

The targets are defined in
[`osac-installer/make/remote-debug.mk`](../../../osac-installer/make/remote-debug.mk).

### Target summary

| Target | Purpose |
|---|---|
| `install-osac-operator` | Upgrade the operator in the existing `osac` Helm release. |
| `enable-debug` | Install the OpenShift remote-debug SCC Helm release. |
| `disable-debug` | Remove that SCC release after checking for active Pods using it. |
| `image-build-debug` | Build the Delve-enabled image for the selected platform. |
| `image-push-debug` | Push the debug image tag. |
| `test-debug-entrypoint` | Check Delve startup behavior for both continue modes. |
| `remote-debug-make-test` | Check focused installer Make workflows without a cluster. |
| `remote-debug-render-test` | Check normal/debug chart renders and SCC policy. |

### `install-osac-operator` variables

This target requires an existing `osac` release and the same `PROFILE` and
namespace used for that installation. It re-renders the umbrella chart from
profile values, `OPERATOR_VALUES_FILE` if supplied, and `EXTRA_HELM_ARGS`; pass
the same custom values used for the original installation to avoid resetting
those settings to profile defaults.

| Variable | Meaning |
|---|---|
| `PLATFORM` | Required: `kind` or `openshift`. Kind loads the image locally; OpenShift pushes it to the registry. |
| `PROFILE` | Required installer profile, such as `dev` or `vmaas-ci`. |
| `NS` | Required namespace containing the existing `osac` release. |
| `REMOTE_DEBUG` | `true` builds/selects the debug image; `false` restores normal settings from the selected profile and values overlays. Defaults to `false`. |
| `REMOTE_DEBUG_IMAGE` | Debug image reference. Required for OpenShift when debugging; Kind defaults to `localhost/osac-operator:latest-debug`. |
| `OPERATOR_VALUES_FILE` | Optional custom values overlay, applied after selected profile values. |
| `EXTRA_HELM_ARGS` | Additional Helm overrides, applied before operator debug-specific settings. |
| `IMAGE_PLATFORM` | `linux/amd64` or `linux/arm64`; defaults to the host architecture. Use `linux/amd64` to cross-build for x86 from an ARM Mac. |
| `DEBUG_CONTINUE` | `true` continues the manager at startup; `false` waits for an IDE attach. Defaults to `true`. |
| `CONTAINER_TOOL` | `docker` or `podman`; the installer selects an available tool if omitted. Log in to the registry with the same tool before an OpenShift push. |

The operator-level image targets can also be used directly:

```bash
make -C osac-operator image-build-debug \
  CONTAINER_TOOL=podman IMAGE_PLATFORM=linux/amd64 \
  DEBUG_IMG=quay.io/<your-org>/osac-operator:dev-debug

make -C osac-operator image-push-debug \
  CONTAINER_TOOL=podman DEBUG_IMG=quay.io/<your-org>/osac-operator:dev-debug
```

`image-build-debug` defaults to the host architecture and Podman; its platform
must be `linux/amd64` or `linux/arm64`. `image-push-debug` pushes the selected
`DEBUG_IMG` tag.

For OpenShift, set `KUBECONFIG` to the intended cluster before running the
installer target. If it is unset, the main installer may select a profile's
Kind kubeconfig.

Example OpenShift debug install for an x86 cluster:

```bash
export KUBECONFIG=/path/to/openshift.kubeconfig
make -C osac-installer enable-debug

make -C osac-installer install-osac-operator \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  REMOTE_DEBUG=true \
  REMOTE_DEBUG_IMAGE=quay.io/<your-org>/osac-operator:dev-debug \
  IMAGE_PLATFORM=linux/amd64
```

For OpenShift, the target builds and pushes the image automatically; registry
login and push permission are required. For Kind, the target builds and loads
the image into the cluster and does not push it.

For Kind, no SCC is needed:

```bash
make -C osac-installer install-osac-operator \
  PLATFORM=kind PROFILE=dev NS=osac REMOTE_DEBUG=true
```

To leave the manager paused until the IDE attaches:

```bash
make -C osac-installer install-osac-operator \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  REMOTE_DEBUG=true DEBUG_CONTINUE=false \
  REMOTE_DEBUG_IMAGE=quay.io/<your-org>/osac-operator:dev-debug \
  IMAGE_PLATFORM=linux/amd64
```

### OpenShift SCC lifecycle

The SCC is required only on OpenShift. `enable-debug` and `disable-debug` need
no `PLATFORM`, `PROFILE`, or `NS` arguments. They use the inherited `KUBECONFIG`,
or `~/.kube/config` if it is unset. The Helm release record lives in
`kube-system`; the SCC itself is cluster-scoped. These targets require the
permissions needed to create and delete cluster-scoped SCC resources.

The current SCC permits **all service accounts in all namespaces** to request
`SYS_PTRACE` and unconfined seccomp. It still denies privileged containers,
host access, and privilege escalation, and requires a read-only root
filesystem. Its priority is `0` (OpenShift rejects negative SCC priorities).
This is a broad, temporary cluster permission; use it only where that scope is
acceptable.

To finish debugging, restore the normal operator first, then remove the SCC:

```bash
make -C osac-installer install-osac-operator \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac REMOTE_DEBUG=false

make -C osac-installer disable-debug
```

`disable-debug` refuses to uninstall while a non-terminal Pod in any namespace
is annotated as using the managed or legacy debug SCC. It does not switch
workloads back to normal security settings. If the manually created legacy SCC
`osac-operator-debug` remains, the target warns but does not delete that
unmanaged resource; remove it only after confirming it belongs to this debug
setup.

## 4. `debug-me` usage

`debug-me` is a standalone companion checkout, separate from the OSAC monorepo.
Install its Python environment once from the helper directory:

```bash
cd ../debug-me
uv sync
cp debug-config.example.yaml debug-config.yaml
```

Set the target configuration as shown above, then start a tunnel to the
currently running Pod:

```bash
./debug.sh start <operator-pod-name> -n osac -c manager
./debug.sh status <operator-pod-name> -n osac -c manager
./debug.sh stop <operator-pod-name> -n osac -c manager
```

If the OpenShift kubeconfig is not the current default, prefix the commands with
`KUBECONFIG=/path/to/openshift.kubeconfig`.

The helper prefers a prestarted headless Delve server, as used by the OSAC debug
image. In that mode, `start` does not install or attach another debugger; it
starts a workstation-side port-forward bound to `127.0.0.1` and prints a VS Code
remote-attach configuration. The printed object belongs inside the
`configurations` array of `.vscode/launch.json`. Its name is
`Remote Debug: [<workload-name>]`, and its source paths come from
`substitutePath`. Process discovery ignores Go's `** telemetry **` subprocess,
which uses the same `dlv` executable name but is not a Delve server.

The helper also supports helper-managed attach for containers without a
prestarted Delve server: it downloads and checksum-verifies a matching Delve
release, uploads it to writable `/tmp`, and attaches to the configured `pid`.
The container then needs the required shell utilities, writable/executable
`/tmp`, `SYS_PTRACE` where needed, and a seccomp/SCC policy that permits tracing.

For a prestarted server, `stop` stops only the local port-forward; Delve and the
manager in the Pod continue running. `status` checks the Delve listener and
tunnel, but a running listener does not guarantee that the manager child is
still alive. Delve is PID 1, so the Pod may remain `Running` after the manager
exits. If the manager exits, restart the operator Deployment to create a new
debug session before attaching again:

```bash
kubectl rollout restart deployment/osac-operator -n osac
```

## 5. Helm differences from a regular deployment

| Debug chart difference | Why it is needed |
|---|---|
| Manager image and command switch to the debug image and `debug-entrypoint`; the debug image adds `manager-debug` and `dlv`. | Delve needs the unoptimized Go binary and starts it as its debuggee. The normal image remains free of the Delve executable. |
| Container port `2345` is declared, but no Service or Route is created. | Delve is accessed only through local port-forwarding; its API is unauthenticated. |
| `--leader-elect=false` in debug; `--leader-elect=true` otherwise. | A breakpoint pauses the manager, including Lease renewal. Disabling election prevents a long intentional pause from causing leader-election loss and manager exit. Debug mode must use one isolated manager to avoid concurrent reconciliation. |
| Manager liveness and readiness probes are omitted. | Delve can be running while its manager is paused for an IDE attach; normal manager probes would fail or cause unwanted restarts. Helm waits for the Delve container, not for the manager to resume. |
| The debug container requests `SYS_PTRACE`, uses unconfined seccomp, and mounts a writable `/tmp` `emptyDir`; the root filesystem remains read-only and all other capabilities are dropped. | Delve needs to trace the Go process and has temporary runtime files. OpenShift admits this only with the debug SCC. |
| Debug mode uses a separate `osac-operator-debug` ServiceAccount, optional debug resource values, and platform-specific `fsGroup` and image-pull policy. | Keeps debug-specific identity and runtime settings out of normal deployments and handles Kind versus OpenShift UID behavior. |
| OpenShift debugging uses the separate Helm-managed `osac-remote-debug` SCC release. | The Pod requests `SYS_PTRACE` and unconfined seccomp; the SCC allows these while continuing to deny privileged containers and host access. Its current `system:serviceaccounts` grant is cluster-wide and must be treated as temporary. |
| Only the manager changes; the console-proxy Deployment remains on the normal image with its regular probes. | The supported debugging scope is the operator manager, not the console proxy. |

The operator's regular behavior is restored by setting `REMOTE_DEBUG=false` in
the focused upgrade; that skips the debug image build/push and restores the
regular manager command, probes, ServiceAccount, and leader election. On
OpenShift, remove the temporary SCC afterward with
`make -C osac-installer disable-debug`.
