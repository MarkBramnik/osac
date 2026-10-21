# Remote debugging OSAC components

This guide describes focused remote-debug workflows for the OSAC Operator and
Fulfillment service. It covers Delve-enabled images, the OpenShift SCC lifecycle,
installer Make targets, the `debug-me` helper, and debug-mode deployment changes.

For a concise user-facing summary, see the
[remote-debugging feature overview](../../features/remote-debugging.md).

## 1. Goal

Remote debugging lets an IDE attach to the OSAC Operator manager or to a
Fulfillment service process running in a Kubernetes or OpenShift Pod. Each
workflow builds a separate debug image, updates the relevant Deployments in the
existing `osac` Helm release, and forwards Delve's port to the developer's
workstation. It does not install duplicate Deployments or create a Service or
Route for Delve.

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

### Fulfillment service image and processes

The Fulfillment service has one image shared by the controller, gRPC server,
REST gateway, and event publisher. The `runtime-debug` Containerfile target adds
the pinned Delve version and a debug entrypoint; the service binary is built
without optimizations or inlining. `install-fulfillment-service` builds and
pushes or loads this one image, then enables `service.debug.enabled` for those four
existing Deployments. MCP, console-proxy, and ingress-proxy containers are not
debug-wrapped.

Each selected Pod runs Delve on `127.0.0.1:2345`. The Pods have separate network
namespaces, so they can use the same Pod-side port. No `hostPort`, Service, or
Route is added. When `DEBUG_CONTINUE=false`, each process waits for an IDE attach;
the Fulfillment target uses this as its default. All four processes are debugged
at once, so APIs, controller reconciliation, and event publishing may pause.
Use a development environment.

| Deployment | Container | Delve port | Suggested workstation port |
|---|---|---:|---:|
| `fulfillment-controller` | `controller` | 2345 | 2346 |
| `fulfillment-grpc-server` | `grpc-server` | 2345 | 2347 |
| `fulfillment-rest-gateway` | `rest-gateway` | 2345 | 2348 |
| `fulfillment-event-publisher` | `event-publisher` | 2345 | 2349 |

The differing workstation ports allow all four `debug-me` tunnels to run at
once. Only these four Deployments receive the debug image, ptrace capability,
unconfined seccomp, writable `/tmp`, and debug-mode probe removal. Their normal
image reference is preserved and selected again when debug is disabled.

### Helm and installer schema

The operator and Fulfillment developer-only `debug` objects are accepted by
narrowly matched `patternProperties` rules, not ordinary `properties` entries,
so they do not add Wizard controls. Both focused targets use the selected
profile values, optional component-specific values files, and
`EXTRA_HELM_ARGS`; debug image and runtime settings are passed as command-line
overrides. They retain normal Helm schema validation and do not reuse stored
release values. The focused upgrades suppress hooks so unrelated AAP bootstrap
and template-publishing jobs aren't rerun. The full `install-osac` workflow
retains its normal hook behavior.

The separate SCC chart has no user-configurable values. It creates the
cluster-scoped SCC `osac-remote-debug` and is installed as Helm release
`osac-debug-scc` in the fixed release-storage namespace `kube-system`.

For `debug-me`, configuration is in `debug-config.yaml`, not the installer
schema. Each `targets` item identifies a workload and container. The config can
contain the operator and all four Fulfillment targets:

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
  - namespace: osac
    workload: {kind: Deployment, name: fulfillment-controller}
    container: controller
    debugPort: 2345
    localPort: 2346
    substitutePath:
      - from: "${workspaceFolder}/fulfillment-service"
        to: "/opt/app-root/src/fulfillment-service"
  - namespace: osac
    workload: {kind: Deployment, name: fulfillment-grpc-server}
    container: grpc-server
    debugPort: 2345
    localPort: 2347
    substitutePath:
      - from: "${workspaceFolder}/fulfillment-service"
        to: "/opt/app-root/src/fulfillment-service"
  - namespace: osac
    workload: {kind: Deployment, name: fulfillment-rest-gateway}
    container: rest-gateway
    debugPort: 2345
    localPort: 2348
    substitutePath:
      - from: "${workspaceFolder}/fulfillment-service"
        to: "/opt/app-root/src/fulfillment-service"
  - namespace: osac
    workload: {kind: Deployment, name: fulfillment-event-publisher}
    container: event-publisher
    debugPort: 2345
    localPort: 2349
    substitutePath:
      - from: "${workspaceFolder}/fulfillment-service"
        to: "/opt/app-root/src/fulfillment-service"
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
| `install-fulfillment-service` | Upgrade the four Delve-enabled Fulfillment Deployments in the existing `osac` release. |
| `enable-debug` | Install the OpenShift remote-debug SCC Helm release. |
| `disable-debug` | Remove that SCC release after checking for active Pods using it. |
| `image-build-debug` | Build the Delve-enabled image for the selected platform. |
| `image-push-debug` | Push the debug image tag. |
| `test-debug-entrypoint` | Check Delve startup behavior for both continue modes. |
| `remote-debug-make-test` | Check focused installer Make workflows without a cluster. |
| `remote-debug-render-test` | Check normal/debug chart renders and SCC policy. |

### `install-fulfillment-service` variables and examples

This target uses the same required `PLATFORM`, `PROFILE`, and `NS` values as
`install-osac-operator`, and requires an existing `osac` release. It builds one
image for all four debugged processes.

| Variable | Meaning |
|---|---|
| `REMOTE_DEBUG` | `true` builds/selects the debug image; `false` restores the normal service image and chart behavior. Defaults to `false`. |
| `REMOTE_DEBUG_IMAGE` | Optional debug image reference. Kind defaults to `localhost/fulfillment-service:latest-debug`; OpenShift requires an explicit registry reference. |
| `FULFILLMENT_VALUES_FILE` | Optional custom values overlay, applied after the selected profile values. |
| `EXTRA_HELM_ARGS` | Existing command-line overrides, applied before debug-specific image settings. |
| `IMAGE_PLATFORM` | `linux/amd64` or `linux/arm64`; defaults to the host architecture. |
| `DEBUG_CONTINUE` | `false` pauses the service processes until attach; `true` starts them immediately. Fulfillment defaults to `false`. |
| `CONTAINER_TOOL` | `docker` or `podman`; the installer selects an available tool if omitted. Log in to the registry with that tool before an OpenShift push. |

OpenShift example for an x86 cluster:

```bash
export KUBECONFIG=/path/to/openshift.kubeconfig
make -C osac-installer enable-debug
make -C osac-installer install-fulfillment-service \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  REMOTE_DEBUG=true \
  REMOTE_DEBUG_IMAGE=quay.io/<your-org>/fulfillment-service:dev-debug \
  IMAGE_PLATFORM=linux/amd64 DEBUG_CONTINUE=false
```

The target builds and pushes the image once. Kind builds and loads the image
locally and does not need the SCC:

```bash
make -C osac-installer install-fulfillment-service \
  PLATFORM=kind PROFILE=dev NS=osac REMOTE_DEBUG=true
```

When finished, run the target with `REMOTE_DEBUG=false`; it omits the debug
overlay and restores the normal profile image/settings. On OpenShift, remove the
shared debug SCC only after no debug Pods use it:

```bash
make -C osac-installer install-fulfillment-service \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac REMOTE_DEBUG=false
make -C osac-installer disable-debug
```

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

To finish debugging, restore normal mode for every component being debugged,
then remove the SCC:

```bash
make -C osac-installer install-osac-operator \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac REMOTE_DEBUG=false
make -C osac-installer install-fulfillment-service \
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
currently running Pod. Use its container name with `-c`:

```bash
./debug.sh start <operator-pod-name> -n osac -c manager
./debug.sh status <operator-pod-name> -n osac -c manager
./debug.sh stop <operator-pod-name> -n osac -c manager
./debug.sh start <fulfillment-pod-name> -n osac -c grpc-server
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

## 5. Operator Helm differences from a regular deployment

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

## 6. Fulfillment Helm differences from a regular deployment

| Debug chart difference | Why it is needed |
|---|---|
| The four supported containers use the shared debug image and `/usr/local/bin/debug-entrypoint`; their normal image remains selected when `service.debug.enabled=false`. | The same binary image runs each service subcommand, so the image is built and pushed once without overwriting the normal repository/tag. |
| Delve listens on `127.0.0.1:2345`; only a `containerPort` is declared. No `hostPort`, Service, or Route is added. | `debug-me` uses authenticated Pod port-forwarding to reach Delve's unauthenticated API. |
| Probes are omitted in debug mode. The controller, gRPC server, and REST gateway retain `RollingUpdate` but use `maxSurge: 0` and `maxUnavailable: 100%`; the event publisher remains `Recreate`. | Debug rollouts stop old Pods before creating replacements, preventing overlapping Delve sessions without changing the strategy type on existing Deployments. |
| Debug containers request `SYS_PTRACE`, use unconfined seccomp, mount writable `/tmp`, and set `HOME`/`TMPDIR` there. Existing read-only root filesystems and dropped capabilities are retained where configured. | Delve needs tracing permissions and temporary writable storage; OpenShift admits these settings only with the temporary debug SCC. |
| `DEBUG_CONTINUE=false` is the Fulfillment target default; set it to `true` to start all four processes immediately. | Paused processes wait for IDE attach, but the service APIs, controller, and event publisher are unavailable until resumed. |

Run the four `debug-me` entries independently. Each uses Pod-side port `2345`;
the provided config assigns local ports `2346`–`2349` so all tunnels can be open
simultaneously. Disabling debug restores the normal service image from the
selected profile and optional custom values; it does not rebuild or push that
image.
