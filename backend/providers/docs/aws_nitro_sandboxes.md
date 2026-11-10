# AWS Nitro Sandboxes on EKS

The AWS EKS provider supports discovery and preflight of explicitly sandboxed
Kubernetes manifests. The manifest parser and renderer also support an explicit
per-workload `nitro_sandbox` block. It uses Kubernetes APIs, with the normal EKS authentication
flow. It does not create a new AWS cloud resource or call a sandbox lifecycle
API. Ordinary manifests retain their existing apply behavior.

## Capability contract

Cluster management and capability refresh add `nitro_sandboxes` to the existing
cluster capabilities JSON:

```json
{
  "status": "detected",
  "runtime_classes": [{"name": "isolated-sandbox", "mode": "isolated"}]
}
```

`detected` means at least one AWS runtime class is installed with its expected
handler. `not_detected` means runtime inventory succeeded with no matching classes;
`unknown` means discovery could not complete, including insufficient RBAC.
Detection describes cluster configuration. It does not verify that a pod ran
inside Nitro, that nodes can schedule it, or that KMS accepted its attestation.
The managed platform ClusterRole grants only `get` and `list` on runtime classes;
operators managing their own RBAC must grant these reads separately.

## Task readiness and explicit enablement

Open `/clusters/<slug>/settings?section=nitro` for the AWS cluster. The
`astroliftNitroTaskReadiness(clusterId)` GraphQL query reports each task mode as
`ready`, `not_ready`, or `unknown`, separately from its saved enabled state.
Both reads and writes require `cluster.manage` in the owning organization;
shared clusters additionally require a platform operator. Team-bound bearers
cannot manage these organization resources.

`configureNitroTasks(input: {clusterId, expectedVersion, expectedSource,
taskModes})` stores the explicit intent in
`provider_config.nitro_sandboxes.task_modes`. Modes default to an empty list.
The mutation rechecks prerequisites, refuses a stale cluster version or source
fingerprint, locks and rechecks write authority, and changes no infrastructure.
Only newly enabled modes require a ready verdict; an enabled mode can still be
disabled when infrastructure becomes unreachable. Readiness and opt-in are
configuration evidence, not proof of attestation.

All EKS paths require version 1.36 or later. An Auto Mode cluster can be ready
for provisioning before any sandbox nodes exist. Manual capacity requires
observed ready, schedulable nodes with the AWS sandbox label and allocatable
slots, the supported kernel/containerd versions, and running EC2 parents with
Nitro Enclaves enabled. RuntimeClass inventory alone remains insufficient.
AWS probes use the cluster credential's effective session, including AssumeRole;
the configured key-admin role must match that effective STS role.

API and worker processes need the identical per-cluster confidential configuration,
signed runtime tools and a writable memory-backed workspace of at least 8 GiB,
with at least 1 GiB currently free. The installer mounts an 8 GiB memory
`emptyDir` at `/dev/shm` on both deployments and reserves that memory in their
pod budgets. The agent namespace comes from `agent_namespace(org_slug)`, not
the installer namespace. Pod Identity must match that namespace, service account,
cluster and runtime role exactly. Source evidence includes the cluster ARN,
namespace, configuration fingerprint and signed tool-manifest SHA.

Workers recheck their own current process before preparing Briefs or task
secrets. API readiness therefore cannot authorize a misconfigured worker. Task
Pods using either Nitro runtime receive exactly the dedicated pool toleration
`astrolift.dev/nitro-only=true:NoSchedule` before admission and confidential
policy generation. Ordinary task Pods receive no such toleration. The installer
owns Auto Mode, its bounded custom NodeClass/NodePool and namespace associations;
the App does not enable global runtime selection or provision capacity.

## Selecting a sandbox in astrolift.toml

```toml
[[workloads]]
name = "worker"
kind = "deployment"
nitro_sandbox = { mode = "isolated" }

[[workloads.containers]]
name = "worker"
image_ref = "registry.example/worker@sha256:..."
```

The block accepts `mode` (`isolated` or `confidential`), `network_isolated`
(boolean, default false), and `policy` (an external generated policy string).
Controller confidential mode requires a nonempty policy. Task-family confidential
agents generate their policy per run and reject a supplied static policy. These values survive normalization,
persist in the registered app's manifest, participate in its hash, and render
onto the workload's pod template without changing other workloads. Omission
preserves the existing normalized shape and Kubernetes output. Normal deploy and
rollback renderers consume the stored raw manifest.

Supported workload kinds are deployment, statefulset, cronjob, task, workflow,
and agent. Service-family agents render a standing Deployment. Task-family
agents support isolated or operator-enabled confidential mode without a custom
policy or network isolation;
the selector is retained for dispatch rather than creating a standing resource.
Knative functions, static sites, provider FaaS and the unimplemented `job`
workload kind reject the block.
Legacy `[[jobs]]` and `[[tasks]]` aliases must use `[[workloads]]` for sandbox
selection. GPU requests are rejected. Network-isolated workloads cannot expose
public services or scrape metrics, and port-serving containers must declare exec
healthchecks explicitly so normalization does not add network probes.

The renderer attaches the supplied confidential policy after its own pod
construction. Later deploy-time injections or admission webhooks can change
policy-pinned fields. The caller remains responsible for generating the policy
from the exact final admitted pod spec and matching the KMS policy; this feature
does not claim to automate confidential policy generation or verify attestation.

## Task-family agent dispatch

For a task-family agent, `nitro_sandbox = { mode = "isolated" }` selects the AWS
runtime when the dispatcher creates its per-run Job. Selection is resolved from
that Workload's own registered app and matching workload name in the stored
normalized manifest. Another workload's selector does not apply. Omission
preserves ordinary Job output and the existing operator runtime setting.

Explicit selection cannot override a different operator `AGENT_RUNTIME_CLASS`,
and a GPU environment spec is refused. Runtime availability and the expected
AWS handler are checked before managed-model or credential provisioning. The
Job retains `runtimeClassName: isolated-sandbox` after pod hardening. Recovery
refuses to adopt an existing Job using a different runtime.

For a task-family agent, `nitro_sandbox = { mode = "confidential" }` selects a
separate `k8s_confidential_pod` backend. The target is frozen before dispatch;
ordinary Jobs cannot adopt confidential Pods. Operator-wide
`AGENT_RUNTIME_CLASS=confidential-sandbox` remains refused, including imported
agents and boxes: it cannot supply this per-run workflow. Static task policies,
network isolation and GPU requests remain refused.

The backend requires a non-root, digest-pinned, baked agent image with contract
label `ai.astrolift.agent-image.contract` at least 7. Boot installs, managed
models, model gateway, VNC and filesystem bindings are unsupported. Callback,
controller, messages and MCP endpoints must use HTTPS. Imported package
extensions do not select this backend; the selector comes from the registered
Astrolift workload manifest.

### Operator configuration

Confidential tasks require explicit per-cluster opt-in above and a valid
`NITRO_CONFIDENTIAL_TASKS_CONFIG` containing
an entry keyed by the exact cluster GUID. Configure API and worker processes
with the same JSON, for example:

```json
{
  "CLUSTER-GUID": {
    "registry": "123456789012.dkr.ecr.us-west-2.amazonaws.com/confidential-runs",
    "key_admin_arn": "arn:aws:iam::123456789012:role/confidential-key-admin",
    "runtime_role_arn": "arn:aws:iam::123456789012:role/confidential-runtime",
    "service_account": "confidential-runner",
    "architecture": "amd64",
    "runtime_directory": "/opt/astrolift/nitro",
    "temporary_root": "/dev/shm"
  }
}
```

Use a dedicated private ECR repository and distinct same-account key-admin and
Pod Identity runtime roles. The runtime role must not administer KMS keys.
The provider checks the AWS account, installed RuntimeClass and exact service
account Pod Identity association before preparing credentials. The operator
must provision those resources, the node runtime and suitable memory-backed
workspace capacity; this App path does not install them. Optional
`resource_tags` can add qualification ownership tags without replacing the
mandatory task and ownership tags.

The backend Docker image installs pinned AWS runtime tools for amd64/arm64,
verifying the pinned manifest, AWS signature, archive and binary hashes. Each
run verifies the installed signed tools again. Preparation resolves the real
Brief and task credentials, adds them and the original executable to a private
OCI layer, and encrypts **every layer** with a new per-run KMS key. Kubernetes
receives an immutable encrypted image, bootstrap entrypoint and public task ID;
it does not receive a plaintext task Secret or task environment. Private image
workspaces are temporary. The encrypted image still needs normal ECR access;
image encryption does not remove registry authorization requirements.

The final named Pod is server dry-run admitted, checked for unsupported
mutations, then passed to signed `eks-nitro-sandbox-genpolicy`. Policies deny
exec and log access. The backend reads the generated policy file and the tool's
PCR17/KMS-condition output, repeats admission to reject subsequent changes,
and grants runtime `kms:Decrypt` only with matching PCR17 and
PCR8 `ServiceName=eks.amazonaws.com`. It applies that exact policy-bound Pod
using create-only semantics. This is policy construction; it is not evidence
that AWS released a key.

Recovery binds the task, callback hash, image digest, policy and Pod hash to a
persisted checkpoint. Stop checks the saved authority and uses the Pod UID
precondition, refusing replacements. Cleanup removes the matching task-tagged
ECR image and schedules its tagged KMS key for deletion after seven days;
failures retain a cleanup-pending checkpoint. Operators must reconcile keys
left by a process crash between KMS creation and checkpoint persistence using
the task/ownership tags. Keep the admin role until keys are PendingDeletion.
Checkpoints explicitly retain `attestation_verified: false`; neither a created
Pod nor successful policy generation upgrades that field.

## Applying an explicitly sandboxed workload

Set the pod's `spec.runtimeClassName` to `isolated-sandbox` or
`confidential-sandbox`. For controllers, set the field inside the pod template.
Preflight covers Pod, Deployment, StatefulSet, DaemonSet, ReplicaSet,
ReplicationController, Job and CronJob. It also runs for dry-run and create-only
applies. Every sandboxed workload is checked before any resource in the batch
is written. RuntimeClass resources must already exist before the batch starts.

Preflight refuses a missing runtime or unexpected handler, `hostNetwork: true`,
`hostUsers: false`, and NVIDIA/AMD GPU requests or limits. A network-isolated
sandbox (`sandbox.amazonaws.com/network-isolated: "true"` on the pod metadata)
must use exec probes instead of HTTP, TCP or gRPC probes. Kubernetes admission
and the AWS runtime remain authoritative for other resource compatibility.

Confidential mode requires a nonempty `sandbox.amazonaws.com/policy` annotation
on the pod metadata. The provider preserves the supplied policy and manifest;
it does not generate, decode, verify, or regenerate the policy. Generate it with
AWS's signed `eks-nitro-sandbox-genpolicy` from the final pod spec, including any
admission mutations. Changes to pinned fields require regeneration. KMS release
of secrets requires the correct PCR17 policy measurement and
`kms:RecipientAttestation:PCR8:ServiceName` set to `eks.amazonaws.com`.
Presence of a policy annotation is a prerequisite, not evidence of attestation.

EKS Auto Mode supplies the runtime classes and provisions compatible nodes.
Standard EKS needs sandbox-enabled AMIs and supported instances. Network-isolated
pods lose DNS, Pod Identity credentials and ordinary networking; logs and exec
use a separate channel, while port-forward and network probes do not work.
Confidential policies can deny exec and other introspection. EBS sharing,
resource resize and device passthrough have additional AWS limitations.

## Support boundary

The provider accepts explicit Kubernetes manifests and the manifest pipeline
accepts the `nitro_sandbox` block above. The gated task backend adds per-run Cedar generation, encrypted-image preparation
and KMS policy management as described above. It does not add dedicated console
controls, CLI commands, automatic runtime/AMI installation or an attestation
verification claim. Live encrypted-image release/refusal qualification remains
required before enabling it for production.

ECS Nitro Sandboxes are a separate integration: Managed Instances only, capacity
provider `instanceLaunchTemplate.nitroEnclavesEnabled=true`, task definition
`trustedExecutionConfiguration.isolationMode` (`ISOLATED_SANDBOX` or
`CONFIDENTIAL_SANDBOX`), and `awsvpc` networking. Confidential tasks require
`attestationPolicy` and verified KMS key conditions. Fargate and arbitrary EC2
launch-type tasks do not gain this setting automatically.

Self-managed EC2 enables `EnclaveOptions.Enabled=true` on a supported parent
instance and runs a compatible Firecracker VMM on that instance. Firecracker's
`enclave` configuration creates the sandbox. That host-side lifecycle and its
image, networking, resource accounting and attestation responsibilities are
outside this EKS provider integration.

## Official references

- [Launch announcement](https://aws.amazon.com/about-aws/whats-new/2026/11/nitro-sandboxes-eks-ecs-ec2/)
- [EKS sandboxing](https://docs.aws.amazon.com/eks/latest/userguide/sandboxing.html)
- [EKS confidential setup](https://docs.aws.amazon.com/eks/latest/userguide/sandboxing-confidential-setup.html)
- [EKS limitations](https://docs.aws.amazon.com/eks/latest/userguide/sandboxing-limitations.html)
- [ECS overview](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ict-overview.html)
- [ECS limitations](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/ict-considerations-and-limitations.html)
- [EC2 Nitro Sandboxes](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/amazon-ec2-nitro-sandboxes.html)

## Local qualification (2026-11-09)

The manifest implementation in `2591031ca` passed the real PostgreSQL
persistence suite in dedicated test database `test_astro_aws_nitro_20261109`:
`test_nitro_sandbox_persist_2668.py` plus the existing `test_persist.py` suite,
**22 passed**, process exit 0, 1434.77 seconds including initial database setup.
The new persistence case verifies exact external-policy preservation after DB
reload and render, changed isolation producing a new manifest hash, and selector
removal leaving no stale normalized block. The manifest source is unchanged
between that checkpoint and provider hardening checkpoint `000baeeb2`.

Additional local qualification:

| Surface | Result |
|---|---|
| Provider/EKS management/shared Kubernetes adapter regressions | 198 passed |
| Manifest parser, normalizer and renderer regressions | 199 passed |
| Latest focused sandbox tests, including terminating runtimes | 52 passed |
| Provider/manifest Ruff checks and focused strict helper mypy | Passed |
| Operator readiness probe | 8 passed |
| Operator overlay | Helm lint passed; rendered API and worker each select the isolated runtime exactly once |

The PostgreSQL run emitted two pytest assertion-rewrite warnings for already
imported `constance` and `anyio` modules; no tests failed. These are local
implementation checks, not a complete repository CI run or a live AWS rollout.
These historical checks did not exercise a real EKS sandbox workload,
confidential KMS release/refusal, ECS task or EC2 host VM lifecycle. The gated
confidential task implementation described above was added subsequently; live
KMS release/refusal was not qualified at that checkpoint; the later live evidence and activation boundary are recorded below.

## Isolated task-family selector qualification (2026-11-09)

Signed implementation checkpoint `6f77b09c1` adds the isolated task-family path
above. A focused serial Django run against the existing dedicated PostgreSQL
test database passed **62 tests**, exit 0, in **27.24 seconds**. It covers new
selector conflicts and recovery checks, existing runtime/pod-hardening/VNC/Job
namespace regressions, and both real persistence cases. The new persistence case
reads the retained selector through the real Workload/app relationship and
renders the selected runtime into the per-run Job. Ordinary rendered output is
also compared against the selected output with only the runtime field added.

The parser follow-up passed **31 tests**. A subsequent imported-package format
guard passed **13 selector-only tests**, exit 0, in **0.33 seconds**, with eight
unrelated cases deselected; that follow-up uses minimal settings without an app
registry or database. Ruff passed on the changed Python files. The focused
Django run emitted the two existing assertion-rewrite warnings for already
imported modules. No broad build/suite, full CI run or live AWS check is implied.

A subsequent recovery-boundary follow-up passed **10 targeted tests**, with
17 deselected, exit 0, in **2.89 seconds**. It covers null/list values at each
Job spec/template/pod-spec boundary, valid and mismatched runtime recovery,
and unchanged ordinary rendered output. Recovery now validates those objects
before callback-credential extraction and reuses the validated pod spec for
runtime binding. Ruff and diff checks passed; no broad suite was rerun.

At this isolated-selector checkpoint, confidential/custom-policy/network-isolated
variants and per-workload selection for imported packages and agent boxes were
still open. The later gated confidential path above addresses policy and image
preparation; custom policies and network isolation remain unsupported.
Dedicated console/CLI controls, automatic EKS runtime provisioning, ECS supported
client integration, EC2 host VM lifecycle and confidential attestation evidence
remain outside this checkpoint. Neither application nor operator branch has
merged or deployed.

## Released Python SDK model evidence (2026-11-09)

The existing providers environment uses boto3/botocore 1.43.72; the backend
Pipfile pins boto3 1.43.71. Both predate the launch. The latest released
[botocore 1.43.126](https://pypi.org/project/botocore/1.43.126/) wheel was inspected
directly through its embedded `botocore/data/ecs/2014-11-13/service-2.json.gz`.
Its `RegisterTaskDefinitionRequest` lacks `trustedExecutionConfiguration`, and
no shape contains `nitroEnclavesEnabled`. The release therefore cannot yet drive
the new ECS contract through the supported boto3 API. No dependency upgrade or
custom injected AWS service model is included merely to bypass validation.

Artifact: `botocore-1.43.126-py3-none-any.whl`, uploaded 2026-11-06T20:48:04Z,
SHA-256 `2f2239a47025a2fa173cd145ab8803ac02b76114864bf5b66475ff1cb88e6b0a`.
[Released wheel](https://files.pythonhosted.org/packages/ad/e2/c51360cea33895b187f50603f1abcababe76a68f05de273d47f0293c6f42/botocore-1.43.126-py3-none-any.whl).
The [official ECS API reference](https://docs.aws.amazon.com/AmazonECS/latest/APIReference/API_TrustedExecutionConfiguration.html)
documents the contract. ECS implementation must wait for a released supported
SDK model or another officially supported client that exposes these fields.


## Confidential task local qualification (2026-11-10)

Signed checkpoint `77f3309d8` passed 113 dispatch, hardening, manifest and real
PostgreSQL persistence tests against dedicated Nitro services; 58 focused
provider/transport tests; and 131 EKS/Kubernetes regression tests. These sets
overlap and must not be added into a single total. Backend Ruff 0.6 lint and
format checks, provider Ruff and strict typing for the two new helpers passed.
Both real pinned architecture archives passed checksum, AWS signature,
extraction and binary verification. A later framing follow-up passed 25 helper
and pipeline tests, including malformed ocicrypt packet refusal.

The full backend suite remains separate verification. Its first run stopped
after 756 passes because no local Temporal server was listening; the exact
failed recovery test passed after starting a dedicated Nitro server. A corrected
full run was started in the background. The broad informational core/config
mypy check still reports existing errors; changed-file diagnostics match the
baseline after accounting for line shifts. A Docker API health timeout blocked
the container build, so neither a backend image build nor container `run.sh`
verification is claimed here. Live EKS key release/refusal and EC2 lifecycle
qualification are tracked separately in the operator repository.


## Reproducing the bounded live qualification

`backend/scripts/qualify_nitro_confidential.py` runs only against the tagged,
private qualification EKS cluster and configured helper role. Its helper source
hashes pin checkpoint `374066149`; its own script revision is independent.
Provide `--config <JSON> --state-directory <private-directory>` and kubeconfig.
The JSON keys are `region`, `cluster`, `run_id`, `repository`, `key_admin_arn`
and `runtime_role_arn`; namespace/service account are fixed to
`qualification/confidential-runner`. Run it as a background job on the owned
Linux helper, preserving its exit marker and private state/diagnostics.

The positive workload asserts a random secret delivered only inside encrypted
layers. The negative workload uses the same image/key, a regenerated policy
with a different PCR17, and the unchanged original key grant. Pod observations
alone never qualify KMS refusal: correlate the unique key and runtime role with
authoritative KMS audit events. No image fetch or policy pins are disabled.

`--resume-negative` reuses the existing positive state without creating a new
positive Pod, image or key. If and only if the Kubernetes API returns 404 for
the positive Pod, `--positive-snapshot-sha256 <operator-verified-SHA256>` permits
the fixed private `positive-observation-private.json` snapshot; the same UID,
task, image, policy, prepared-hash and success guards still apply. A present
replacement Pod or another API error cannot use this fallback. Preserve the
original positive source revision and audit provenance. After collecting the
negative audit evidence, explicit `--cleanup` verifies Pod ownership, removes
the run image and confirms the key is PendingDeletion. Keep its admin role
until every tagged test key is retired.


## Live qualification and activation boundary (2026-11-10)

The bounded amd64 qualification completed on a disposable private EKS 1.37
Auto Mode cluster. The encrypted agent asserted its secret and exited zero.
Fourteen accepted KMS Decrypt audit events recorded the predicted PCR17 and
`PCR8:ServiceName=eks.amazonaws.com`. A changed-policy Pod using the same image
and Enabled key failed to start at KMS Decrypt. Two CloudTrail AccessDenied
events correlate to its runtime role, key and controlled unchanged-grant window;
one has the exact request ID from the runtime failure. Denials redact recipient
measurements, so the negative PCR17 is a signed-generator prediction, not an
independently observed denied-call measurement.

Direct-mode EC2 start, recovery, guest boot, stop and cleanup also passed on a
disposable parent. This does not qualify confidential EC2 orchestration. All
disposable compute and network resources were removed; the three task keys were
verified PendingDeletion for November 17. The immutable operator receipts are:

- [Direct EC2 lifecycle](https://github.com/calliopeai/astrolift-opscode/blob/0c57d4f490d09432b5602e6269dc5d2f4d409953/aws/qualification/nitro-direct-20261110.json)
- [Confidential release and refusal](https://github.com/calliopeai/astrolift-opscode/blob/0c57d4f490d09432b5602e6269dc5d2f4d409953/aws/qualification/nitro-confidential-20261110.json)
- [Resource cleanup](https://github.com/calliopeai/astrolift-opscode/blob/0c57d4f490d09432b5602e6269dc5d2f4d409953/aws/qualification/nitro-cleanup-20261110.json)

Qualification does not install or enable Nitro on an existing cluster. Deploy
an application build containing this implementation, prepare genuine sandbox
runtime/node capacity, and run preflight from a network location that can reach
the target Kubernetes API. Then opt in a workload through `nitro_sandbox`.
Confidential tasks additionally require the ECR, role, Pod Identity and
cluster-GUID operator configuration described above on both API and workers.
Do not use an operator-wide runtime default as a shortcut for preparing a mixed
cluster fleet.

Installer-managed activation is still a separate integration. The installer
already shares plain environment between API and workers, so it can carry the
configuration. Its existing standard EKS node provisioning does not install
Nitro runtime capacity or provision these confidential-task prerequisites.
A supported installer flow must explicitly provision/reconcile compatible node
capacity, runtime readiness, the private run repository, distinct trusted
key-admin and parent-visible runtime permissions, and Pod Identity. It must
surface unknown readiness honestly, preserve per-workload opt-in, and retain
key retirement authority until every owned task key is PendingDeletion.
A console enablement switch and automatic prerequisite reconciliation are not
implemented by these App checkpoints.
