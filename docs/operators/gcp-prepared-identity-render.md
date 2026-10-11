# Private prepared GCP deployment rendering

This renderer is a staged private bridge, not an activated deployment workflow.
It does not apply resources, establish a controller incarnation, start a rollout,
or prove workload identity, inference or readiness. Existing deployments with no
prepared input keep their current render behavior on every provider.

`prepared_identity_for_deployment` reads actual committed preparation and IAM
journals under their current joint locks. Preparation must be terminal
`OBSERVED` at the desired revision; its stored annotation fence must match the
current completed IAM journal. The selected environment must appear in exactly
one stored logical subject. Namespace and KSA UIDs come from the committed
preparation ledger. GSA email and unique ID come from the original identity
context, never the current app or organization slug.

The caller must hold both journal mutexes and supply **both** current callbacks.
Each must return exactly `None`; boolean callbacks are refused. The preparation
callback must re-admit the original caller and complete current accepted source,
alias and deployment-origin context after the last lock. The IAM callback must
also validate its current original-authority and accepted-union fence. Metadata
shape, GUIDs, hashes and a constructed `PreparedGCPIdentity` are not admission.
A later production caller must thread the protected `DeploymentAuthorityContext`
through these callbacks and freshly recheck before native effects. This bridge
provides no permissive production callback or public receipt input.

Pass the resulting metadata through the existing
`render_resources_for_deployment(..., prepared_gcp_identity=...)` private keyword.
The renderer uses the recorded physical namespace, omits Namespace and
ServiceAccount writes, and stamps the original KSA and owner GUID labels after
image digests, literal-secret digests, managed binding revisions and placement
have been rendered. It never derives a replacement GSA email or KSA from a slug.
Foreign labels and annotations remain; conflicting identity, owner, namespace,
KSA or recorded UID metadata refuses before output. User-supplied controller
UID, resourceVersion, generation or ownerReferences cannot establish an original
native controller incarnation.

For example, a legacy no-receipt render may emit a slug-derived ServiceAccount
and select it in the pod template. A prepared render instead selects the recorded
KSA and emits no identity object. Changing the app slug does not change that
recorded identity in this renderer. This is **not whole-pipeline rename support**:
the current Endpoint plan producer still derives current logical namespace/KSA
names, and changing original physical subjects requires a separate reviewed
identity-evolution protocol.

## Supported output and fingerprints

Strict rendering supports `apps/v1` Deployment and StatefulSet with an explicit
integer replica count from 1 through 256, and normal `apps/v1` DaemonSet with no
`spec.replicas`. It refuses HPA, zero or missing static replica counts, standalone
Pods, ReplicaSets, Jobs and CronJobs. Selectors must use the runtime observer's
supported forms and match their pod-template labels. Bounds are 256 resources,
16 controllers and 2 MiB of canonical JSON per resource.

Supported ancillary resources are core-v1 Service, Secret, ConfigMap and PVC;
networking-v1 Ingress and NetworkPolicy; monitoring-v1 PodMonitor; and
Gateway-v1 Gateway/HTTPRoute. Other resources and API versions refuse. This
allowlist does not prove ancillary apply or health coverage: all required app
resources need guarded apply evidence before a future workflow reports success.

`prepared_controller_fingerprints` returns only bounded names, selector text,
replica count and SHA-256 digests of the final controller request, execution
pod template and placement constraints. It retains no Secret body, raw auth,
PodSpec, controller UID or generation. Controller requests can contain secret
references and execution data, so a hash is an integrity binding, not a privacy
redaction protocol or authorization proof. Future apply must separately admit
any reviewed Kubernetes defaults and refuse unexplained admission mutations.
Raw pre-apply hashes cannot establish that the native accepted template is equal.

DaemonSet render metadata deliberately has `replicas=None`.
[Kubernetes defines desiredNumberScheduled in DaemonSet status](https://kubernetes.io/docs/reference/kubernetes-api/apps/daemon-set-v1/),
not as a spec replica count. Later guarded apply and current observation must
bind a real desired count of at least one to the original native controller
UID/generation and unchanged admitted pool/placement ceiling. Unknown or zero
counts remain pending. This renderer does not fabricate a `WorkloadTarget` or
prove that later count admission.

Controller apply journaling, exact execution/current-source admission, native
response UID persistence, ancillary effects and runtime acceptance remain
separate #2278 prerequisites. In particular, a lost create without acknowledged
and committed original UID evidence stays unknown; matching names or operation
markers do not establish its incarnation or permit a resend.
