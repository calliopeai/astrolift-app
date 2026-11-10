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
Confidential mode requires a nonempty policy. These values survive normalization,
persist in the registered app's manifest, participate in its hash, and render
onto the workload's pod template without changing other workloads. Omission
preserves the existing normalized shape and Kubernetes output. Normal deploy and
rollback renderers consume the stored raw manifest.

Supported workload kinds are deployment, statefulset, cronjob, task, workflow,
and agent. Service-family agents render a standing Deployment. Task-family
agents support only isolated mode without a custom policy or network isolation;
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

Confidential mode, nonempty custom policies and network-isolated task agents
remain rejected at parse and dispatch. Per-run task IDs, Brief/secret/model
bindings and admission changes require a final-spec policy workflow; static
precursor policies must not be treated as complete confidential-task support.
The new selector applies to task-family agent Jobs registered from an Astrolift
workload manifest. Imported agent package extensions are not interpreted as
Astrolift workload selectors; those agents retain operator runtime behavior.
Agent boxes continue to use
the existing operator-wide runtime setting and do not inherit this selector.

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
accepts the `nitro_sandbox` block above. This checkpoint does not add dedicated
console controls, CLI commands, automatic runtime/AMI installation, Cedar generation, encrypted-image tooling, KMS policy
management, attestation verification or a cloud execution smoke test.

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
No real EKS sandbox workload, confidential KMS key release/refusal, ECS task or
EC2 host VM lifecycle has been exercised. Task-family confidential/custom-policy/network-isolated selection,
confidential generation/attestation automation and the other support boundaries
above remain open.

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

Remaining task-specific gaps are confidential/custom-policy/network-isolated
variants and per-workload selection for imported packages and agent boxes.
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
