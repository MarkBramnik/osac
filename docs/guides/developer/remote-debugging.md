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
selected only when `DEBUG=true` is passed to the focused installer target.

## 2. Architecture and configuration

### Image and process flow

The Containerfile has separate normal and debug build stages:

1. `builder` builds the normal manager, console proxy, and trust-admission
   binaries.
2. `debug-builder` builds `manager-debug` with optimizations and inlining
   disabled, and builds the `dlv` executable.
3. The normal `release` image contains the regular binaries. The separate
   `debug` target adds `manager-debug`, `dlv`, and the shared
   `tools/debug-entrypoint.sh`, configured to launch `manager-debug`.

See the [operator Containerfile](../../../osac-operator/Containerfile) and
[shared debug entrypoint](../../../tools/debug-entrypoint.sh). The Fulfillment
debug image uses the same script, configured for
`/usr/local/bin/fulfillment-service`.

The debug Pod starts the entrypoint, which `exec`s Delve as PID 1. Both debug
images pin Delve to IPv4 loopback, `127.0.0.1:2345`, inside the container; the
listen address cannot be overridden. Delve launches
`/usr/local/bin/manager-debug` as its debuggee. `DEBUG_CONTINUE=true` continues
the manager immediately; `DEBUG_CONTINUE=false` leaves it paused until the IDE
attaches. The console proxy remains on the normal image.

The access path is:

```text
IDE -> 127.0.0.1:<localPort> -> debug-me transport
    -> default: kubectl/oc port-forward -> Pod port 2345
    -> optional exec: kubectl/oc exec -> Bash /dev/tcp/127.0.0.1:2345
    -> Delve (PID 1) -> manager-debug
```

Delve's API is unauthenticated, so it is bound to IPv4 loopback in the Pod. The
`debug-me` helper uses port-forward by default. Its optional `transport: exec`
mode uses authenticated exec streaming and Bash's IPv4 `/dev/tcp` support,
bypassing a cluster port-forward backend that dials `localhost` as IPv6. Do not
expose Delve with a Service or Route.

### Fulfillment service image and processes

The Fulfillment service has one image shared by the controller, gRPC server,
REST gateway, and event publisher. The `runtime-debug` Containerfile target adds
the pinned Delve version and a debug entrypoint; the service binary is built
without optimizations or inlining. `build-fulfillment-service` builds and pushes
or loads this one image; `install-services` enables `service.debug.enabled` for those four
existing Deployments. MCP, console-proxy, and ingress-proxy containers are not
debug-wrapped.

Each selected Pod runs Delve on IPv4 loopback, `127.0.0.1:2345`. The Pods have
separate network namespaces, so they can use the same Pod-side port. No
`hostPort`, Service, or Route is added. When `DEBUG_CONTINUE=false`, each
process waits for an IDE attach;
the Fulfillment target uses this as its default. All four processes are debugged
at once, so APIs, controller reconciliation, and event publishing may pause.
The CSI chart uses the same Delve entrypoint for its controller and node driver;
pausing the node DaemonSet can interrupt storage operations on every node. Use a
development environment.

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
so they do not add Wizard controls. The component image-build targets do not
use Helm. `install-services` uses selected profile values, optional values files,
and `EXTRA_HELM_ARGS` for the umbrella release; it delegates CSI to the separate
`install-csi-driver` release. The umbrella upgrade retains normal Helm schema
validation and suppresses hooks so unrelated AAP bootstrap and
template-publishing jobs aren't rerun. The full `install-osac` workflow retains
its normal hook behavior.

The separate SCC chart creates the cluster-scoped SCC `osac-remote-debug` and
is installed as Helm release `osac-debug-scc` in the fixed release-storage
namespace `kube-system`. `enable-debug` scopes its grant to operator and
Fulfillment debug ServiceAccounts in the target OSAC namespace (default
`osac`) and the CSI controller debug ServiceAccount in `CSI_DRIVER_NAMESPACE`
(default `osac-csi`). The CSI node debug identity uses the CSI chart's own node
SCC.

For `debug-me`, configuration is in `debug-config.yaml`, not the installer
schema. Each `targets` item identifies a workload and container. The config can
contain the operator, all four Fulfillment targets, and CSI controller/node
targets:

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
  - namespace: osac-csi
    workload: {kind: Deployment, name: csi-driver-controller}
    container: osac-csi-driver
    debugPort: 2345
    localPort: 2350
    substitutePath:
      - from: "${workspaceFolder}/osac-csi-driver"
        to: "/opt/app-root/src/osac-csi-driver"
  - namespace: osac-csi
    workload: {kind: DaemonSet, name: csi-driver-node}
    container: osac-csi-driver
    debugPort: 2345
    localPort: 2351
    substitutePath:
      - from: "${workspaceFolder}/osac-csi-driver"
        to: "/opt/app-root/src/osac-csi-driver"
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
| `build-osac-operator` | Build a normal or Delve-enabled operator image; push by default. No Helm or cluster access. |
| `build-fulfillment-service` | Build a normal or shared Delve-enabled Fulfillment image; push by default. No Helm or cluster access. |
| `build-csi-driver` | Build a normal or Delve-enabled CSI driver image; push by default. No Helm or cluster access. |
| `install-services` | Apply selected operator/Fulfillment settings in the `osac` release and delegate selected `csi-driver` to its independent Helm release. |
| `install-csi-driver` | Independently build/load and install or upgrade the CSI chart release (defaults: release `csi-driver`, namespace `osac-csi`). Does not require an existing `osac` release. |
| `enable-debug [NS=<namespace>]` | Install the OpenShift remote-debug SCC for operator/Fulfillment accounts in `NS` (default: `osac`) and the CSI controller debug account in `CSI_DRIVER_NAMESPACE` (default: `osac-csi`). |
| `disable-debug` | Remove that SCC release after checking for active Pods using it. |
| `remote-debug-scc-integration-test` | Manually verify SCC admission and the removal guard on an explicitly approved OpenShift cluster. |
| `remote-debug-make-test` | Check image and install Make workflows without a cluster. |
| `remote-debug-render-test` | Check normal, combined-debug chart renders and SCC policy. |

### Image build and push variables

The build targets select the normal image when `DEBUG=false` and the
Delve-enabled image when `DEBUG=true`. They do not install anything.

| Variable | Meaning |
|---|---|
| `DEBUG` | `true` builds the debug image; `false` builds the normal image. Defaults to `false`. |
| `PUSH` | Push the image after building. Defaults to `true`; set `false` for local builds. When pushing, supply the component's image reference. |
| `IMAGE_PLATFORM` | Target architecture, e.g. `linux/amd64` or `linux/arm64`; defaults to the host architecture. |
| `CONTAINER_TOOL` | `docker` or `podman`; defaults to the installer-selected tool. |
| `OSAC_OPERATOR_IMAGE` | Operator image reference. Overrides the matching entry in the home config; required for a selected build when `PUSH=true`. |
| `FULFILLMENT_SERVICE_IMAGE` | Fulfillment image reference. Overrides the matching entry in the home config; required for a selected build when `PUSH=true`. |
| `CSI_DRIVER_SERVICE_IMAGE` | CSI driver image reference. Overrides its normal/debug image-config entry; required for a selected build when `PUSH=true`. |

With `PUSH=false`, the build goals use local tags by default:
`localhost/osac-operator:latest[-debug]`,
`localhost/fulfillment-service:latest[-debug]`, and
`localhost/osac-csi-driver:latest[-debug]`. Kind can load these during
`install-services` or `install-csi-driver`. An OpenShift cluster must be able to
pull the image, so use and push a registry reference there.

`install-services` builds selected images by default (`BUILD=true`). It resolves
each image reference from the command line first, then from
`~/.config/osac/image-mappings.json`. The optional JSON file keeps separate
normal and debug references for the operator, Fulfillment, and CSI driver, so
switching `DEBUG` selects the corresponding image set:

```json
{
  "version": 1,
  "images": {
    "normal": {
      "osac-operator": "quay.io/<your-org>/osac-operator:dev",
      "fulfillment-service": "quay.io/<your-org>/fulfillment-service:dev",
      "csi-driver": "quay.io/<your-org>/osac-csi-driver:dev"
    },
    "debug": {
      "osac-operator": "quay.io/<your-org>/osac-operator:dev-debug",
      "fulfillment-service": "quay.io/<your-org>/fulfillment-service:dev-debug",
      "csi-driver": "quay.io/<your-org>/osac-csi-driver:dev-debug"
    }
  }
}
```

Use `IMAGE_CONFIG=/path/to/file.json` to select a different config file. A
command-line image variable overrides only that component's value; the other
component can still use its config entry. With `BUILD=false`, no image is built
or pushed. The component-specific continue variables affect runtime startup,
not the image build.

Example: build and push both debug images for an x86 OpenShift cluster. Log in
with the same container tool before building:

```bash
export KUBECONFIG=/path/to/openshift.kubeconfig
make -C osac-installer enable-debug NS=osac

make -C osac-installer build-osac-operator \
  DEBUG=true IMAGE_PLATFORM=linux/amd64 \
  OSAC_OPERATOR_IMAGE=quay.io/<your-org>/osac-operator:dev-debug
make -C osac-installer build-fulfillment-service \
  DEBUG=true IMAGE_PLATFORM=linux/amd64 \
  FULFILLMENT_SERVICE_IMAGE=quay.io/<your-org>/fulfillment-service:dev-debug
make -C osac-installer build-csi-driver \
  DEBUG=true IMAGE_PLATFORM=linux/amd64 \
  CSI_DRIVER_SERVICE_IMAGE=quay.io/<your-org>/osac-csi-driver:dev-debug
```

Normal images can also be built independently, including for a non-native
platform:

```bash
make -C osac-installer build-osac-operator \
  DEBUG=false PUSH=false IMAGE_PLATFORM=linux/amd64
```

### Install selected services

`install-services` updates the existing umbrella release for selected
`osac-operator`/`fulfillment-service` components and calls the independent
`install-csi-driver` target for `csi-driver`. The CSI target upgrades the
`csi-driver` Helm release in `osac-csi` by default; it does not add CSI to the
umbrella chart. If CSI is the only selected service, `install-services` skips
the umbrella upgrade. The two releases upgrade independently, so a failure can
leave one updated and the other unchanged. `SERVICES` is a comma-separated set
of `osac-operator`, `fulfillment-service`, and `csi-driver`. With `DEBUG=true`,
each selected service uses its debug image; the list should describe the full
debug set you intend to leave enabled.

Use the same profile and custom values as the existing release. Because Helm
renders the whole umbrella release, omitted custom values can still revert to
profile defaults; `install-services` does not use stored release values.

`install-services` generates one fresh UTC timestamp per invocation and applies
it to each selected service's `kubectl.kubernetes.io/restartedAt` pod-template
annotation. This forces a rollout even if an image was rebuilt and pushed under
the same tag, and it also applies with `BUILD=false`. Selecting
`fulfillment-service` updates the annotation on all four Fulfillment Deployments;
selecting `osac-operator` updates the manager Deployment; selecting `csi-driver`
updates the CSI controller Deployment and node DaemonSet. The Fulfillment event
publisher uses a `Recreate` strategy, so it stops before its replacement starts.
Rollouts terminate existing debugger sessions; in debug mode, false continue
settings leave the corresponding replacement processes waiting for IDE attach.

| Variable | Meaning |
|---|---|
| `PLATFORM`, `PROFILE`, `NS` | Required deployment platform, existing installer profile, and namespace containing the `osac` release. |
| `SERVICES` | Comma-separated component set: `osac-operator`, `fulfillment-service`, `csi-driver`, or any combination. When debugging, treat this as the complete set to leave in debug mode. |
| `DEBUG` | `true` enables debug for every listed service; `false` disables debug and selects normal images. Defaults to `false`. |
| `OSAC_OPERATOR_IMAGE` | Tagged operator image reference. Required for the operator when debugging on OpenShift; optional normal-image override otherwise. |
| `FULFILLMENT_SERVICE_IMAGE` | Tagged shared Fulfillment image reference. Required for Fulfillment when debugging on OpenShift; optional normal-image override otherwise. |
| `CSI_DRIVER_SERVICE_IMAGE` | Tagged CSI image reference. Required for CSI when debugging on OpenShift; optional normal-image override otherwise. |
| `CSI_DRIVER_RELEASE`, `CSI_DRIVER_NAMESPACE` | Separate CSI Helm release and namespace; default to `csi-driver` and the chart's fixed `osac-csi` namespace. |
| `OPERATOR_VALUES_FILE`, `FULFILLMENT_VALUES_FILE` | Optional umbrella-chart values overlays applied after the selected profile. |
| `CSI_DRIVER_VALUES_FILE` | Optional CSI chart values overlay for its separate release. Existing release values are reused on upgrade, then this file is applied. |
| `EXTRA_HELM_ARGS` | Additional umbrella-chart Helm overrides, applied before debug settings. |
| `BUILD` | Automatically build the listed services before the upgrade. Defaults to `true`; use `false` when the images are already built. |
| `PUSH` | When `BUILD=true`, push built images. Defaults to `true`; `PUSH=false` is for local Kind images and is rejected for OpenShift builds. |
| `IMAGE_PLATFORM` | Forwarded to each selected build target, e.g. `linux/amd64` or `linux/arm64`. |
| `CONTAINER_TOOL` | Container tool forwarded to the selected build targets. |
| `CSI_DRIVER_VERSION`, `CSI_DRIVER_GIT_COMMIT` | Optional CSI binary version/commit metadata used by its image build. |
| `IMAGE_CONFIG` | Optional alternate JSON config path. Defaults to `~/.config/osac/image-mappings.json`. |
| `KUBECONFIG`, `KIND_CLUSTER_NAME` | Select the target cluster; set `KUBECONFIG` explicitly for OpenShift. |
| `CONTINUE` | Sets the component continue settings. Defaults to `true`, so selected processes start without waiting for IDE attach. |
| `DEBUG_CONTINUE` | Operator behavior: `true` continues at startup; `false` waits for IDE attach. Defaults to `CONTINUE` (`true`). |
| `FULFILLMENT_DEBUG_CONTINUE` | Fulfillment behavior: `true` continues at startup; `false` waits for IDE attach. Defaults to `CONTINUE` (`true`). |
| `CSI_DRIVER_DEBUG_CONTINUE` | CSI controller behavior; defaults to `CONTINUE`. |
| `CSI_DRIVER_NODE_DEBUG_CONTINUE` | CSI node DaemonSet behavior; defaults to `CSI_DRIVER_DEBUG_CONTINUE`. `false` pauses the driver on every node and can disrupt all CSI operations; the target prints a warning. |

The build targets remain available for standalone builds. When invoked through
`install-services`, `BUILD`, `PUSH`, `IMAGE_PLATFORM`, and `CONTAINER_TOOL` are
forwarded to selected build targets; continue variables configure runtime
behavior. Explicit image variables override matching mode entries in the JSON
config, and resolved references are used for both build and deployment.

Run the CSI target by itself to install or update its separate release. It uses
the same installer `PLATFORM`, `PROFILE`, and `NS` parameters; `PROFILE` selects
the Kind context, while `CSI_DRIVER_NAMESPACE` selects the chart namespace.

```bash
make -C osac-installer install-csi-driver \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  DEBUG=true IMAGE_PLATFORM=linux/amd64 \
  CSI_DRIVER_SERVICE_IMAGE=quay.io/<your-org>/osac-csi-driver:dev-debug \
  CSI_DRIVER_VALUES_FILE=values/my-csi-driver.yaml
```

For OpenShift, install the SCC first. Then build, push, and apply selected debug
images. The image references can come from the JSON config; explicit variables
override them:

```bash
make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  DEBUG=true SERVICES=osac-operator,fulfillment-service,csi-driver \
  IMAGE_PLATFORM=linux/amd64
```

For Kind, the target loads local images before upgrading the relevant release.
No registry references are required for this local build:

```bash
make -C osac-installer install-services \
  PLATFORM=kind PROFILE=dev NS=osac \
  BUILD=true PUSH=false DEBUG=true \
  SERVICES=osac-operator,fulfillment-service,csi-driver \
  IMAGE_PLATFORM=linux/amd64
```

To use already-built images, set `BUILD=false`. On Kind, the referenced image
must already be available to the local container tool so the target can load it.
On OpenShift, the image must be pullable by the cluster. To restore profile
normal images without rebuilding them:

```bash
make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  BUILD=false DEBUG=false SERVICES=osac-operator,fulfillment-service,csi-driver
```

`CONTINUE` defaults to `true`, so selected processes start immediately. For CSI,
`CSI_DRIVER_DEBUG_CONTINUE` controls the controller and
`CSI_DRIVER_NODE_DEBUG_CONTINUE` controls every node DaemonSet instance. Pausing
the node instances can stop CSI operations across the cluster. `BUILD`, `PUSH`,
and `IMAGE_PLATFORM` are also accepted by `install-services`. Remove the SCC
only after no debug Pods use it.

### OpenShift SCC lifecycle

The SCC is required only on OpenShift. `enable-debug` defaults to namespace
`osac`; pass `NS=<namespace>` to select another OSAC namespace.
`disable-debug` needs no namespace. Neither target needs `PLATFORM` or
`PROFILE`. They use the inherited `KUBECONFIG`, or
`~/.kube/config` if it is unset. The Helm release record lives in `kube-system`;
the SCC itself is cluster-scoped. These targets require the permissions needed
to create and delete cluster-scoped SCC resources.

The SCC grants `SYS_PTRACE` and unconfined seccomp only to
`system:serviceaccount:<NS>:osac-operator-debug`,
`system:serviceaccount:<NS>:fulfillment-debug`, and
`system:serviceaccount:<CSI_DRIVER_NAMESPACE>:csi-driver-controller-debug`; it
has no service-account group grant. The charts create each identity when its
component is debugged, so Kind needs no SCC setup. The operator debug account
receives operator RBAC; Fulfillment and CSI controller debug accounts do not
share it. The CSI node debug identity uses the CSI chart's node SCC, which
retains the privileges the node plugin already needs. The shared debug SCC still
denies privileged containers, host access, and privilege escalation, and
requires a read-only root filesystem. Its priority is `0` (OpenShift rejects
negative SCC priorities).

To verify real OpenShift admission and the `disable-debug` guard, run the
installer's manual integration test only on an approved disposable or
admin-coordinated cluster. It creates a temporary namespace and cluster-scoped
SCC, proves all three named debug-account Pods are admitted with that SCC, verifies
removal is blocked while either Pod is running, then removes the Pods and SCC:

```bash
OSAC_DEBUG_SCC_IT_APPROVED=true \
KUBECONFIG=/path/to/approved-openshift.kubeconfig \
make -C osac-installer remote-debug-scc-integration-test
```

The test refuses to run if the managed or legacy SCC, its Helm release, or its
temporary namespace already exists. It is manual and is not part of Kind CI.

The CSI attach smoke test is also manual and destructive to its temporary CSI
namespace. It requires an approved disposable Kind cluster and refuses to run
if the namespace, release, or cluster-scoped `CSIDriver` already exists:

```bash
OSAC_CSI_DEBUG_IT_APPROVED=true \
KUBECONFIG="$HOME/.kube/osac-dev-kind.kubeconfig" \
KIND_CLUSTER_NAME=osac-dev \
make -C osac-csi-driver remote-debug-integration-test
```

It builds and loads the debug image, pauses the CSI controller and node driver,
tests two consecutive Delve attaches to the controller and every node Pod, then
uninstalls its release and deletes `osac-csi`.

To finish debugging, restore normal mode for every component being debugged,
then remove the SCC:

```bash
make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  BUILD=false DEBUG=false SERVICES=osac-operator,fulfillment-service
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
starts the configured transport with a workstation-side listener bound to
`127.0.0.1` and prints a VS Code remote-attach configuration. The default
transport is `port-forward`; set `transport: exec` in a target's
`debug-config.yaml` to opt into the IPv4 exec tunnel. The printed object belongs inside the
`configurations` array of `.vscode/launch.json`. Its name is
`Remote Debug: [<workload-name>]`, and its source paths come from
`substitutePath`. Process discovery ignores Go's `** telemetry **` subprocess,
which uses the same `dlv` executable name but is not a Delve server.

The helper also supports helper-managed attach for containers without a
prestarted Delve server: it downloads and checksum-verifies a matching Delve
release, uploads it to writable `/tmp`, and attaches to the configured `pid`.
The container then needs the required shell utilities, writable/executable
`/tmp`, `SYS_PTRACE` where needed, and a seccomp/SCC policy that permits tracing.

For a prestarted server, `stop` stops only the local tunnel; Delve and the
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
| Container port `2345` is declared, but no Service or Route is created. | Delve is accessed by local port-forward by default or the authenticated exec tunnel when selected; its API is unauthenticated and remains loopback-only. |
| Delve uses `--accept-multiclient` whether the debuggee starts paused or continues. | The tunnel can verify the RPC endpoint before IDE attach, and a disconnected IDE can reconnect without restarting the Pod. Delve remains loopback-only. |
| `--leader-elect=false` in debug; `--leader-elect=true` otherwise. | A breakpoint pauses the manager, including Lease renewal. Disabling election prevents a long intentional pause from causing leader-election loss and manager exit. Debug mode must use one isolated manager to avoid concurrent reconciliation. |
| Manager liveness and readiness probes are omitted. | Delve can be running while its manager is paused for an IDE attach; normal manager probes would fail or cause unwanted restarts. Helm waits for the Delve container, not for the manager to resume. |
| The debug container requests `SYS_PTRACE`, uses unconfined seccomp, and mounts a writable `/tmp` `emptyDir`; the root filesystem remains read-only and all other capabilities are dropped. | Delve needs to trace the Go process and has temporary runtime files. OpenShift admits this only with the debug SCC. |
| Debug mode uses separate `osac-operator-debug` and `fulfillment-debug` ServiceAccounts with token automount enabled, plus optional debug resource values and platform-specific `fsGroup` and image-pull policy. | Each component keeps its own identity on Kind and OpenShift; only the operator account receives operator RBAC. |
| OpenShift debugging uses the separate Helm-managed `osac-remote-debug` SCC release, scoped to operator/Fulfillment debug accounts in `<NS>` and the CSI controller debug account in `<CSI_DRIVER_NAMESPACE>`. | The Pod requests `SYS_PTRACE` and unconfined seccomp; the SCC allows these while continuing to deny privileged containers and host access. The CSI node account remains under its own node SCC. No service-account group is granted access. |
| Only the manager changes; the console-proxy Deployment remains on the normal image with its regular probes. | The supported debugging scope is the operator manager, not the console proxy. |

The operator's regular behavior is restored by setting `DEBUG=false` in
the focused upgrade; that skips the debug image build/push and restores the
regular manager command, probes, ServiceAccount, and leader election. On
OpenShift, remove the temporary SCC afterward with
`make -C osac-installer disable-debug`.

## 6. Fulfillment Helm differences from a regular deployment

| Debug chart difference | Why it is needed |
|---|---|
| The four supported containers use the shared debug image and `/usr/local/bin/debug-entrypoint`; their normal image remains selected when `service.debug.enabled=false`. | The same binary image runs each service subcommand, so the image is built and pushed once without overwriting the normal repository/tag. |
| Delve listens on IPv4 loopback `127.0.0.1:2345`; only a `containerPort` is declared. No `hostPort`, Service, or Route is added. | `debug-me` uses port-forward by default or authenticated exec streaming when the target opts into it; the API remains unauthenticated and loopback-only. |
| Probes are omitted in debug mode. The controller, gRPC server, and REST gateway retain `RollingUpdate` but use `maxSurge: 0` and `maxUnavailable: 100%`; the event publisher remains `Recreate`. | Debug rollouts stop old Pods before creating replacements, preventing overlapping Delve sessions without changing the strategy type on existing Deployments. |
| Debug containers request `SYS_PTRACE`, use unconfined seccomp, mount writable `/tmp`, and set `HOME`/`TMPDIR` there. Existing read-only root filesystems and dropped capabilities are retained where configured. | Delve needs tracing permissions and temporary writable storage; OpenShift admits these settings only with the temporary debug SCC. |
| `CONTINUE=true` is the default for operator, Fulfillment, and CSI processes; CSI has independent controller and node continue overrides. | Paused processes wait for IDE attach; pausing CSI node processes can disrupt storage operations across all nodes. |

Run the four `debug-me` entries independently. Each uses Pod-side port `2345`;
the provided config assigns local ports `2346`–`2349` so all tunnels can be open
simultaneously. Disabling debug restores the normal service image from the
selected profile and optional custom values; it does not rebuild or push that
image.
