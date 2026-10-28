# Remote debugging OSAC components

This is a unified, opt-in development workflow for building and deploying
component images. It provides common controls for normal or Delve-enabled builds,
cross-platform compilation, and image reference management, alongside the
debugging helper and deployment settings.

Currently integrated components are the OSAC Operator, Fulfillment service, and
CSI driver. Other components may be added later if the project decides to
integrate them with this workflow; support is not automatic for every component.
For setup details and the variable reference, see the
[developer guide](../guides/developer/remote-debugging.md).

> **Development only:** This feature is intended for development and
> troubleshooting, not production use. Do not deploy Delve-enabled images or
> debugging settings in production.

## 1. Goal

Debugging helps developers trace execution flow across components, find and
diagnose bugs, and learn how the system behaves. The workflow attaches to the
operator manager, Fulfillment controller/gRPC/REST/event-publisher processes,
or CSI controller and node processes in their existing workloads. Normal images
do not include or run Delve.

## 2. Architecture and configuration

With debug enabled, the target container runs Delve as PID 1. Delve launches the
unoptimized Go process and listens on IPv4 loopback (`127.0.0.1:2345`) inside the
Pod and accepts reconnecting IDE sessions. The `debug-me` helper uses
`port-forward` by default; a target can opt into an IPv4-only `exec` tunnel to
bypass a cluster port-forward backend that resolves `localhost` to IPv6. No
Service or Route exposes Delve. Operator and Fulfillment are updated through the
`osac` umbrella release. CSI remains a separate Helm release, `csi-driver` in
`osac-csi` by default; `install-services` delegates CSI to `install-csi-driver`
and skips the umbrella upgrade when CSI is the only selected service.
Fulfillment uses one shared debug image for its four supported Deployments; CSI
uses one image for its controller Deployment and node DaemonSet. `CONTINUE=true`
is the default. CSI has independent controller and node continue flags; pausing
the node DaemonSet can interrupt storage operations across all nodes.

The `debug-me` helper uses a target entry in `debug-config.yaml`; its source
mapping lets VS Code map local files to the paths recorded in the debug binary:

```yaml
targets:
  - namespace: osac
    workload: {kind: Deployment, name: osac-operator}
    container: manager
    debugPort: 2345
    substitutePath:
      - from: "${workspaceFolder}/osac-operator"
        to: "/opt/app-root/src/osac-operator"
```

The operator and Fulfillment developer-only `debug` objects are validated
through `patternProperties`, not exposed as Enclave Wizard controls. CSI has its
own chart values and release. Image build targets do not invoke Helm; use
`install-services` or `install-csi-driver` to apply images/debug settings. Don't
toggle debug from the Wizard.

For Fulfillment, `debug-me` has one entry per Deployment/container. Each Delve
listener uses Pod port `2345`; the local config assigns workstation ports
`2346`–`2349` so the four tunnels can run concurrently. CSI uses the same Pod
port for its controller and every node DaemonSet Pod; assign separate local
ports if tunneling to more than one at a time.

## 3. Commands

### OpenShift

Set the target kubeconfig, log in to your registry, and install the temporary
SCC before enabling debug:

```bash
export KUBECONFIG=/path/to/openshift.kubeconfig
make -C osac-installer enable-debug NS=osac CSI_DRIVER_NAMESPACE=osac-csi

make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  DEBUG=true SERVICES=osac-operator,fulfillment-service,csi-driver \
  OSAC_OPERATOR_IMAGE=quay.io/<your-org>/osac-operator:dev-debug \
  FULFILLMENT_SERVICE_IMAGE=quay.io/<your-org>/fulfillment-service:dev-debug \
  CSI_DRIVER_SERVICE_IMAGE=quay.io/<your-org>/osac-csi-driver:dev-debug \
  IMAGE_PLATFORM=linux/amd64 CONTINUE=true
```

`install-services` builds and pushes selected images by default. It upgrades the
`osac` release for operator/Fulfillment and delegates CSI to its separate
`csi-driver` release in `osac-csi`; these upgrades are independent. Image
references can instead be stored in `~/.config/osac/image-mappings.json`;
explicit image variables override those entries. `SERVICES` is the complete set
of components to debug.

> **Why use the unified `install-services` workflow?**
>
> Before this workflow, developers had to build and tag component images
> separately, manage pushes and image references by hand, and make manual Helm
> or Deployment edits to switch images and configure Delve, tracing permissions,
> probes, and startup behavior. That was error-prone and required manually
> undoing those changes afterward.
>
> `install-services` provides common controls for normal/debug image flavors,
> cross-platform builds, image mapping and push behavior, then applies the
> matching Helm settings and rolls out the selected existing resources. This
> includes the separate CSI release; no manual Deployment editing is needed.
> This streamlined path is for development and troubleshooting only, not
> production.

The image-mapping file separates normal and debug image references by service.
For example:

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

The operator and Fulfillment charts create separate debug ServiceAccounts; only
the operator account has operator RBAC. The CSI chart creates separate
controller/node debug identities and retains node privileges through its own SCC.
On OpenShift, the shared debug SCC grants the CSI controller identity `SYS_PTRACE`
without granting it operator permissions.
Each selected service is forced through a rollout on every invocation, even if
the pushed image reuses its previous tag. Selecting `fulfillment-service` rolls
all four of its Deployments and disconnects existing IDE sessions.

When finished, restore normal mode and remove the SCC only after no debug Pods
use it:

```bash
make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac BUILD=false DEBUG=false \
  SERVICES=osac-operator,fulfillment-service,csi-driver
make -C osac-installer disable-debug
```

### Kind

Kind does not need the SCC or a registry push. The target builds to local
default tags and loads images before the corresponding release upgrades:

```bash
make -C osac-installer install-services \
  PLATFORM=kind PROFILE=dev NS=osac \
  BUILD=true PUSH=false DEBUG=true \
  SERVICES=osac-operator,fulfillment-service,csi-driver
```

`install-services` loads local images into Kind before deployment. Normal images can also be built without debug, including for a
non-native architecture:

```bash
make -C osac-installer build-fulfillment-service \
  DEBUG=false PUSH=false IMAGE_PLATFORM=linux/amd64
```

The CSI Helm release can also be managed independently of the umbrella:

```bash
make -C osac-installer install-csi-driver \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  DEBUG=true IMAGE_PLATFORM=linux/amd64 \
  CSI_DRIVER_RELEASE=csi-driver CSI_DRIVER_NAMESPACE=osac-csi \
  CSI_DRIVER_SERVICE_IMAGE=quay.io/<your-org>/osac-csi-driver:dev-debug \
  CSI_DRIVER_VALUES_FILE=values/my-csi-driver.yaml
```

`CSI_DRIVER_DEBUG_CONTINUE` controls the controller. The node DaemonSet has its
own `CSI_DRIVER_NODE_DEBUG_CONTINUE` override; set it to `false` only when you
intend to pause the driver on every node, which can disrupt cluster-wide CSI
operations.

## 4. `debug-me` usage

From the companion `debug-me` checkout, start a tunnel to the running manager
Pod, inspect it, and stop the local tunnel when done:

```bash
./debug.sh start <operator-pod-name> -n osac -c manager
./debug.sh status <operator-pod-name> -n osac -c manager
./debug.sh stop <operator-pod-name> -n osac -c manager
./debug.sh start <fulfillment-pod-name> -n osac -c grpc-server
./debug.sh start <csi-controller-pod-name> -n osac-csi -c osac-csi-driver
./debug.sh start <csi-node-pod-name> -n osac-csi -c osac-csi-driver
```

`start` reuses the prestarted Delve server in the OSAC debug image and uses the
configured transport. The default is Kubernetes `port-forward`; set
`transport: exec` on a target to create an IPv4-only exec tunnel that avoids
the cluster port-forward backend. It prints a VS Code remote-attach
configuration named `Remote Debug: [osac-operator]`; place it inside the
`configurations` array in `.vscode/launch.json`. `stop` only stops the local
transport for this prestarted-server mode.

The interactive TUI is available with `./debug.sh tui` (or `./debug tui`). It
groups Pods by configured workload, including the CSI controller Deployment and
node DaemonSet. Select a Pod row and press `x` or Space to start/stop its tunnel;
the DaemonSet session is pinned to the selected node. Press `i` for a two-tab IDE
setup modal (VS Code first, GoLand second); `y` copies the active tab's
configuration or instructions when a native clipboard tool is installed. The
VS Code snippet uses the active local port, or the configured port while
disconnected.

Use `/` to filter by namespace, workload, container, Pod, node, status, or image;
`c` clears the filter and `r` refreshes discovery. The TUI reconnects its
prestarted-Delve tunnel after Pod/container restarts; reattach the IDE after the
replacement Pod connects. Sessions and logs are in memory. Pressing `q` stops
local tunnels and exits without changing cluster workloads; Esc closes the modal
but does not exit the TUI.

## 5. Helm differences from a regular deployment

- The manager uses the Delve-enabled image and debug entrypoint; the
  console-proxy deployment remains unchanged.
- Debug mode requests `SYS_PTRACE`, uses unconfined seccomp, and mounts a
  writable `/tmp`; the temporary OpenShift SCC permits tracing without granting
  privileged containers or host access.
- Manager probes are omitted so Delve can wait at attach/breakpoints without
  Kubernetes restarting the container. Leader election is disabled only in
  debug mode so a long breakpoint does not expire the manager's Lease.
- Use one isolated debug manager and ensure no other OSAC operator instance
  reconciles the same resources. Normal mode keeps leader election enabled.
- Delve is available only through local port-forwarding by default or the
  authenticated exec tunnel when explicitly selected; it binds to IPv4 loopback
  and the chart creates no Service or Route for it.
