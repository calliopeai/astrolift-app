# AWS Nitro Sandboxes on EKS

The AWS EKS provider supports discovery and preflight of explicitly sandboxed
Kubernetes manifests. It uses Kubernetes APIs, with the normal EKS authentication
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
handler. `not_detected` means both reads succeeded with no matching classes;
`unknown` means discovery could not complete, including insufficient RBAC.
Detection describes cluster configuration. It does not verify that a pod ran
inside Nitro, that nodes can schedule it, or that KMS accepted its attestation.
The managed platform ClusterRole grants only `get` and `list` on runtime classes;
operators managing their own RBAC must grant these reads separately.

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

This provider checkpoint accepts explicit Kubernetes manifests. It does not
add `astrolift.toml` sandbox selectors, console controls, CLI commands, automatic
runtime/AMI installation, Cedar generation, encrypted-image tooling, KMS policy
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
