# Remote debugging OSAC components

This feature provides IDE connections to the OSAC Operator manager and the four
supported Fulfillment service processes running inside a cluster. For full setup
details and variable reference, see the
[developer guide](../guides/developer/remote-debugging.md).

## 1. Goal

Debug the operator manager or the Fulfillment controller, gRPC server, REST
gateway, and event publisher in their existing Deployments. The normal images do
not include or run Delve.

## 2. Architecture and configuration

With debug enabled, the target container runs Delve as PID 1. Delve launches the
unoptimized Go process and listens on IPv4 loopback (`127.0.0.1:2345`) inside the
Pod and accepts reconnecting IDE sessions. The `debug-me` helper uses
`port-forward` by default; a target can opt into an IPv4-only `exec` tunnel to
bypass a cluster port-forward backend that resolves `localhost` to IPv6. No
Service or Route exposes Delve. One `install-services` invocation updates the existing `osac` Helm
release rather than creating duplicate Deployments. Fulfillment uses one shared
debug image for its four supported Deployments. `CONTINUE=true` is the default;
set `CONTINUE=false` to pause the selected processes until IDE attach.

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

Fulfillment's developer-only `debug` object is validated through
`patternProperties`, not exposed as an Enclave Wizard control. The image build
targets do not invoke Helm; `install-services` applies the selected image and
debug settings for all requested components in one upgrade. Don't toggle debug
from the Wizard.

For Fulfillment, `debug-me` has one entry per Deployment/container. Each Delve
listener uses Pod port `2345`; the local config assigns workstation ports
`2346`–`2349` so the four tunnels can run concurrently.

## 3. Commands

### OpenShift

Set the target kubeconfig, log in to your registry, and install the temporary
SCC before enabling debug:

```bash
export KUBECONFIG=/path/to/openshift.kubeconfig
make -C osac-installer enable-debug NS=osac

make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  DEBUG=true SERVICES=osac-operator,fulfillment-service \
  OSAC_OPERATOR_IMAGE=quay.io/<your-org>/osac-operator:dev-debug \
  FULFILLMENT_SERVICE_IMAGE=quay.io/<your-org>/fulfillment-service:dev-debug \
  IMAGE_PLATFORM=linux/amd64 CONTINUE=false
```

`install-services` builds and pushes the selected images by default, then
performs one upgrade of the existing `osac` release with normal schema
validation and hooks suppressed. Image references can instead be stored in
`~/.config/osac/image-mappings.json`; explicit image variables override
those entries. Use the same profile and custom values as the existing release.
`SERVICES` is the complete set of components to debug; components omitted from
it return to profile values.
The umbrella chart creates separate `osac-operator-debug` and
`fulfillment-debug` ServiceAccounts for the selected components. The operator
debug identity carries operator RBAC; Fulfillment debug pods do not share it.
Each selected service is forced through a rollout on every invocation, even if
the pushed image reuses its previous tag. Selecting `fulfillment-service` rolls
all four of its Deployments and disconnects existing IDE sessions.

When finished, restore normal mode and remove the SCC only after no debug Pods
use it:

```bash
make -C osac-installer install-services \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac BUILD=false DEBUG=false \
  SERVICES=osac-operator,fulfillment-service
make -C osac-installer disable-debug
```

### Kind

Kind does not need the SCC or a registry push. The target builds to local
default tags and loads the images before the single upgrade:

```bash
make -C osac-installer install-services \
  PLATFORM=kind PROFILE=dev NS=osac \
  BUILD=true PUSH=false DEBUG=true \
  SERVICES=osac-operator,fulfillment-service
```

`install-services` loads the local images into Kind before its single Helm
upgrade. Normal images can also be built without debug, including for a
non-native architecture:

```bash
make -C osac-installer build-fulfillment-service \
  DEBUG=false PUSH=false IMAGE_PLATFORM=linux/amd64
```

## 4. `debug-me` usage

From the companion `debug-me` checkout, start a tunnel to the running manager
Pod, inspect it, and stop the local tunnel when done:

```bash
./debug.sh start <operator-pod-name> -n osac -c manager
./debug.sh status <operator-pod-name> -n osac -c manager
./debug.sh stop <operator-pod-name> -n osac -c manager
./debug.sh start <fulfillment-pod-name> -n osac -c grpc-server
```

`start` reuses the prestarted Delve server in the OSAC debug image and uses the
configured transport. The default is Kubernetes `port-forward`; set
`transport: exec` on a target to create an IPv4-only exec tunnel that avoids
the cluster port-forward backend. It prints a VS Code remote-attach
configuration named `Remote Debug: [osac-operator]`; place it inside the
`configurations` array in `.vscode/launch.json`. `stop` only stops the local
transport for this prestarted-server mode.

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
