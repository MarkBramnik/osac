# OSAC CSI Driver

> [!WARNING]
> Be mindful of the content you commit to this repository. Do not commit any
> material containing Red Hat confidential content, including information about
> future product development plans.

The OSAC CSI driver is an aggregating meta-driver that presents a single CSI
identity (`csi.osac.openshift.io`) to Kubernetes and routes storage requests to
vendor-specific CSI drivers (NetApp Trident, VAST, Pure Storage) based on
storage tier resolution from the OSAC fulfillment service.

## Architecture

The driver runs in two modes controlled by Kubernetes deployment topology:

- **Controller plugin** (Deployment): Handles `CreateVolume`, `DeleteVolume`,
  `ControllerPublishVolume`, and `ControllerUnpublishVolume` by resolving the
  storage tier via the fulfillment service and proxying to the appropriate
  vendor CSI controller.

- **Node plugin** (DaemonSet): Handles `NodeStageVolume`, `NodePublishVolume`,
  and related RPCs by routing to vendor node plugins based on the `osac.backend`
  key in the volume context set at creation time.

## Build

```bash
make build        # Build the binary
make test         # Run tests
make lint         # Run golangci-lint
make image-build  # Build container image
make image-push   # Push container image
make image-build-debug IMAGE_PLATFORM=linux/amd64
make image-push-debug
make test-debug-entrypoint
make remote-debug-render-test
```

## Remote debugging

The installer supports `csi-driver` as a service in `SERVICES` and exposes a
standalone `install-csi-driver` target. It upgrades the separate CSI Helm
release (the current install is release `csi-driver` in namespace `osac-csi`);
the CSI chart is not part of the `osac` umbrella release.

```bash
make -C ../osac-installer install-csi-driver \
  PLATFORM=openshift PROFILE=vmaas-ci NS=osac \
  DEBUG=true IMAGE_PLATFORM=linux/amd64 \
  CSI_DRIVER_SERVICE_IMAGE=quay.io/<your-org>/osac-csi-driver:debug
```

The same image is used by the controller Deployment and node DaemonSet. Both
continue by default. `CSI_DRIVER_DEBUG_CONTINUE=false` pauses the controller;
`CSI_DRIVER_NODE_DEBUG_CONTINUE=false` pauses the driver on **every node** and
can disrupt CSI operations cluster-wide. On OpenShift, enable the remote-debug
SCC first with `make -C ../osac-installer enable-debug NS=osac`.

To run the actual attach/reattach smoke test, use a disposable Kind cluster with
no existing `osac-csi` namespace, `csi-driver` release, or
`csi.osac.openshift.io` CSIDriver:

```bash
OSAC_CSI_DEBUG_IT_APPROVED=true \
KUBECONFIG="$HOME/.kube/osac-dev-kind.kubeconfig" \
KIND_CLUSTER_NAME=osac-dev make remote-debug-integration-test
```

The test builds and loads the image, installs a temporary CSI release, attaches
twice to the controller and every node Pod, then removes its release and
namespace. It refuses to run without the approval variable and matching Kind
context.

## Configuration

| Flag | Default | Description |
|------|---------|-------------|
| `--csi-endpoint` | `unix:///csi/osac/csi.sock` | CSI endpoint this driver listens on |
| `--node-id` | (required) | Process startup node ID; NodeGetInfo uses `NODE_NAME` |
| `--driver-name` | `csi.osac.openshift.io` | CSI driver name |
| `--fulfillment-endpoint` | (empty, uses stub) | gRPC endpoint for the OSAC fulfillment service |
| `--fulfillment-client-id` | (empty) | OAuth2 client ID for fulfillment-service authentication |
| `--fulfillment-client-secret-file` | (empty) | Path to file containing the OAuth2 client secret |
| `--fulfillment-issuer-url` | (empty) | Keycloak issuer URL for `client_credentials` token exchange |
| `--fulfillment-ca-file` | (empty) | PEM CA bundle for verified fulfillment-service and OAuth TLS connections |
| `--grpc-insecure` | `false` | Skip TLS server certificate verification |
| `--vendor-sockets` | (empty) | Comma-separated `backend=socketpath` pairs |

The node DaemonSet supplies `NODE_NAME` from the Kubernetes downward API. The
LVMS socket is routed through the existing vendor socket map and defaults to
`/run/topolvm/csi-topolvm.sock`. An explicit `lvms` entry in `--vendor-sockets`
replaces that default; `OSAC_LVMS_NODE_SOCKET` takes precedence over both.
The chart leaves `node.lvmsNodeSocket` empty by default, so it preserves
`node.vendorSockets`. Set `node.lvmsNodeSocket` to supply an explicit environment
override. For custom paths, also align `node.lvmsNodeSocketDir` and
`node.lvmsNodeSocketHostPath` so the socket is accessible inside the container.

### Disabling fulfillment trust or rolling back

The trust admission webhook uses `failurePolicy: Fail` and rejects updates that
remove the `osac.openshift.io/fulfillment-trust-client` label while the webhook
is active. Before changing `global.fulfillmentTrust.enabled` from `true` to
`false`, or rolling back to a chart version without trust admission, remove the
CSI release's `ValidatingWebhookConfiguration` first. Find it with the CSI
release label and delete the `*-fulfillment-trust` resource:

```bash
CSI_RELEASE=your-csi-release
kubectl get validatingwebhookconfigurations \
  -l "app.kubernetes.io/instance=${CSI_RELEASE}" \
  -o custom-columns=NAME:.metadata.name
TRUST_WEBHOOK_NAME=copy-name-ending-in-fulfillment-trust-from-output
kubectl delete validatingwebhookconfiguration "$TRUST_WEBHOOK_NAME"
```

Delete only the `*-fulfillment-trust` resource shown for that release. Its name
uses the chart fullname plus `-fulfillment-trust`; the fullname defaults to
`<release>-csi-driver` unless the release name already contains `csi-driver` or
`fullnameOverride` is set. After deleting the webhook, upgrade or roll back the
CSI release normally.

## License

Apache License 2.0. See [LICENSE](LICENSE).
