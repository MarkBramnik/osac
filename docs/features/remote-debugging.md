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
unoptimized Go process and listens on `127.0.0.1:2345` inside the Pod. The
developer connects through a local port-forward; no Service or Route exposes
Delve. The installer updates the existing `osac` Helm release rather than
creating duplicate Deployments. Fulfillment uses one shared debug image for its
four supported Deployments; its debug-only values overlay enables
`service.debug.enabled`, and `DEBUG_CONTINUE=false` pauses them until IDE attach.

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
`patternProperties`, not exposed as an Enclave Wizard control. The focused Make
target conditionally applies `values/remote-debug-fulfillment.yaml`; don't
toggle debug from the Wizard.

For Fulfillment, `debug-me` has one entry per Deployment/container. Each Delve
listener uses Pod port `2345`; the local config assigns workstation ports
`2346`–`2349` so the four tunnels can run concurrently.

## 3. Commands

### OpenShift

Set the kubeconfig for the target cluster and log in to the registry with the
container tool you use, then install the temporary SCC and enable the debug
image:

```bash
export KUBECONFIG=/path/to/openshift.kubeconfig
make -C osac-installer enable-debug
make -C osac-installer install-osac-operator \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  REMOTE_DEBUG=true \
  REMOTE_DEBUG_IMAGE=quay.io/<your-org>/osac-operator:dev-debug \
  IMAGE_PLATFORM=linux/amd64

make -C osac-installer install-fulfillment-service \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  REMOTE_DEBUG=true DEBUG_CONTINUE=false \
  REMOTE_DEBUG_IMAGE=quay.io/<your-org>/fulfillment-service:dev-debug \
  IMAGE_PLATFORM=linux/amd64
```

The install targets build and push the image. Fulfillment builds one shared
image for its four supported processes. `IMAGE_PLATFORM` selects `linux/amd64`
or `linux/arm64`; use `linux/amd64` to build for an x86 cluster from an ARM Mac.
`DEBUG_CONTINUE=false` leaves the operator manager or Fulfillment processes
paused until attach.

When finished, restore normal mode for each component you debugged, then remove
the SCC. To restore both:

```bash
make -C osac-installer install-osac-operator \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac REMOTE_DEBUG=false
make -C osac-installer install-fulfillment-service \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac REMOTE_DEBUG=false
make -C osac-installer disable-debug
```

The SCC is cluster-scoped. `enable-debug` installs Helm release
`osac-debug-scc`, which creates `osac-remote-debug`; it currently permits all
service accounts in all namespaces to request `SYS_PTRACE` and unconfined
seccomp. Use it only temporarily where that broad scope is acceptable.
`disable-debug` refuses to remove it while non-terminal Pods still use the
managed or legacy debug SCC.

The same SCC supports the Fulfillment debug profile. Use
`install-fulfillment-service` to enable or disable it; the command builds and
pushes one shared image on OpenShift, or builds and loads it for Kind.

### Kind

Kind does not need the SCC targets:

```bash
make -C osac-installer install-osac-operator \
  PLATFORM=kind PROFILE=dev NS=osac REMOTE_DEBUG=true
```

Fulfillment uses the same platform/profile/namespace flags and defaults to
waiting for the IDE before continuing the four processes:

```bash
make -C osac-installer install-fulfillment-service \
  PLATFORM=kind PROFILE=dev NS=osac REMOTE_DEBUG=true
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

`start` reuses the prestarted Delve server in the OSAC debug image and forwards
its port to workstation loopback. It prints a VS Code remote-attach
configuration named `Remote Debug: [osac-operator]`; place it inside the
`configurations` array in `.vscode/launch.json`. `stop` only stops the local
port-forward for this prestarted-server mode.

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
- Delve is available only through port-forwarding; the chart creates no Service
  or Route for it.
