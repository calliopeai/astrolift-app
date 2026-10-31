# Astrolift internal docs: where to look

Operator-facing engineering docs for the Astrolift control plane
(`astrolift-app`). Pages here describe the product as shipped on `main`; each
names the code it rests on. Repo conventions, local stack and the
schema-is-the-contract rule live in [`../bootstrap.md`](../bootstrap.md),
backend specifics in [`../backend/bootstrap.md`](../backend/bootstrap.md).

Public reference for the same product is on astrolift.dev; use it for
anything user-facing rather than duplicating it here:
[`astrolift.toml`](https://astrolift.dev/reference/astrolift-toml/),
[`astro` CLI](https://astrolift.dev/reference/cli/),
[GraphQL and REST API](https://astrolift.dev/reference/api/),
[workflow TOML](https://astrolift.dev/reference/workflow-toml/),
[agent packages](https://astrolift.dev/reference/agent-packages/),
[MCP](https://astrolift.dev/reference/mcp/), and the operator
[install](https://astrolift.dev/operators/install/) and
[runbooks](https://astrolift.dev/operators/runbooks/) pages. The Terraform and
Helm install path is `astrolift-opscode`; the guided installer is
`calliope-installer`.

Conventions on every page: the preconditions an operator needs (account or
role, cluster or container, repo path) are stated at the top; the command that
proves the procedure worked is at the end; placeholders are named in angle
brackets with the command that produces them.

## Install and first start

| I need to | Page |
|---|---|
| Go from nothing to a live URL on a dev install | [quickstart.md](quickstart.md) |
| Know what runs on every container start (first admin claim, IdP, first cluster, apps zone, licence modules) | [operators/install-bootstrap-and-product-deployments.md](operators/install-bootstrap-and-product-deployments.md) |
| Bring a cluster into management, rotate credentials, decommission | [runbooks/cluster-management.md](runbooks/cluster-management.md) |
| Install the cluster agent on a private cluster | [operators/private-cluster-agent-install-1696.md](operators/private-cluster-agent-install-1696.md) |
| Re-point a cluster and keep its workload identity | [operators/cluster-repoint-workload-identity.md](operators/cluster-repoint-workload-identity.md) |
| Let clients discover the install | [operators/public-install-discovery.md](operators/public-install-discovery.md) |
| Turn features on or off per install | [operators/installation-feature-controls.md](operators/installation-feature-controls.md) |
| Size the control plane for more load | [operators/scale-out-readiness.md](operators/scale-out-readiness.md) |

## Upgrade

| I need to | Page |
|---|---|
| Roll the control plane forward (migrations, paired releases) | [operators/control-plane-upgrades.md](operators/control-plane-upgrades.md) |
| Move a preserved ingress-nginx install to the Envoy edge | [operators/ingress-nginx-cutover-2117.md](operators/ingress-nginx-cutover-2117.md) |
| Re-sync managed CI templates after a generation bump | [operators/managed-monorepo-ci.md](operators/managed-monorepo-ci.md), [operators/ci-workflow-region.md](operators/ci-workflow-region.md) |

## Domains and certificates

| I need to | Page |
|---|---|
| Register a zone, provision its wildcard certificate, delegate it, fix a stalled zone, IAM the worker needs | [operators/managed-domains.md](operators/managed-domains.md) |
| Diagnose DNS, delegation, HTTPS for a zone | [operators/managed-domain-diagnostics.md](operators/managed-domain-diagnostics.md) |
| Connect a Cloudflare zone or delegate a child from Cloudflare | [operators/cloudflare-dns-connections.md](operators/cloudflare-dns-connections.md) |
| Route a zone to a public Lambda app | [operators/managed-domain-app-routing.md](operators/managed-domain-app-routing.md) |

## Deployments and rollback

| I need to | Page |
|---|---|
| Deploy, watch, roll back, terminate a stuck rollback, dismiss a failed row | [runbooks/deploy.md](runbooks/deploy.md) |
| Read a deployment's phase history and persisted logs | [operators/deployment-run-history.md](operators/deployment-run-history.md) |
| Understand what a redeploy pins | [operators/redeploy-source-pins.md](operators/redeploy-source-pins.md) |
| Review an environment before restart or scale | [operators/workload-environment-actions.md](operators/workload-environment-actions.md) |
| Environments sharing one namespace | [operators/shared-environment-namespaces.md](operators/shared-environment-namespaces.md) |
| Open a shell in an app environment | [operators/app-exec-targets.md](operators/app-exec-targets.md) |
| Preview environments: exact identity and reads | [operators/exact-preview-targets.md](operators/exact-preview-targets.md), [operators/preview-names-and-workload-identity.md](operators/preview-names-and-workload-identity.md) |
| Pipelines: which cluster runs a job, Knative function scaling | [operators/pipeline-cluster-routing-and-functions.md](operators/pipeline-cluster-routing-and-functions.md) |
| Autoscaling of workloads and nodes | [operators/workload-and-node-autoscaling.md](operators/workload-and-node-autoscaling.md) |
| GPU workloads | [operators/gpu-workloads.md](operators/gpu-workloads.md) |
| Native GKE deploys (guarded apply, Endpoint identity, stuck journals) | [operators/gcp-native-configuration-executor.md](operators/gcp-native-configuration-executor.md), [operators/gcp-guarded-app-apply.md](operators/gcp-guarded-app-apply.md), [operators/gcp-endpoint-workload-identity.md](operators/gcp-endpoint-workload-identity.md) |
| Azure workload identity, adopting Azure resources | [operators/azure-workload-identity.md](operators/azure-workload-identity.md), [runbooks/adopt-azure-managed-resource.md](runbooks/adopt-azure-managed-resource.md) |
| Managed services and their native bindings | [operators/native-managed-service-bindings.md](operators/native-managed-service-bindings.md), [operators/vertex-endpoint-lifecycle.md](operators/vertex-endpoint-lifecycle.md), [operators/bedrock-shared-connections.md](operators/bedrock-shared-connections.md), [operators/bedrock-provisioned-throughput.md](operators/bedrock-provisioned-throughput.md) |
| Temporary apps, image retention | [operators/faas-image-retention.md](operators/faas-image-retention.md) |

## Agents, workflows and runtime

| I need to | Page |
|---|---|
| Dispatch an agent and follow its task | [runbooks/agent-dispatch.md](runbooks/agent-dispatch.md), [runbooks/agent-task-events.md](runbooks/agent-task-events.md), [runbooks/agent-task-artifacts.md](runbooks/agent-task-artifacts.md), [runbooks/agent-task-completion-callbacks.md](runbooks/agent-task-completion-callbacks.md) |
| Chained and native workflows, MCP, schedules | [runbooks/chained-agent-workflows.md](runbooks/chained-agent-workflows.md), [runbooks/workflow-configuration.md](runbooks/workflow-configuration.md), [runbooks/workflow-mcp.md](runbooks/workflow-mcp.md), [runbooks/atomic-workflow-review.md](runbooks/atomic-workflow-review.md), [../backend/docs/reviewed-start-contracts.md](../backend/docs/reviewed-start-contracts.md) |
| Agent host protocol, RuntimeClass, policy and quarantine | [agent-host.md](agent-host.md), [agent-runtime-class.md](agent-runtime-class.md), [agent-enforcement.md](agent-enforcement.md) |
| Toolchain images, environment specs, secrets, task logs | [operators/agent-toolchain-images.md](operators/agent-toolchain-images.md), [operators/agent-environment-spec-home.md](operators/agent-environment-spec-home.md), [operators/agent-secret-owners.md](operators/agent-secret-owners.md), [operators/agent-task-log-pages.md](operators/agent-task-log-pages.md), [operators/agent-dispatch-finalization-2234.md](operators/agent-dispatch-finalization-2234.md) |
| Dispatch clients (IDE, box, MCP) | [operators/dispatch-clients.md](operators/dispatch-clients.md) |
| Connect Zentinelle (organization or install), cluster gateways | [operators/zentinelle-connect.md](operators/zentinelle-connect.md) |
| Model hosting and connections | [operators/vllm-model-hosting.md](operators/vllm-model-hosting.md), [operators/vllm-cpu-avx2-runtime.md](operators/vllm-cpu-avx2-runtime.md), [operators/shared-model-serving.md](operators/shared-model-serving.md), [operators/local-model-artifacts.md](operators/local-model-artifacts.md), [operators/model-connection-approvals.md](operators/model-connection-approvals.md), [operators/model-catalogue-observations.md](operators/model-catalogue-observations.md), [operators/model-playground.md](operators/model-playground.md), [operators/foundry-model-discovery.md](operators/foundry-model-discovery.md) |
| Builder API and brains | [builder-api.md](builder-api.md), [operators/builder-brain-source.md](operators/builder-brain-source.md), [operators/brain-typed-read-tokens.md](operators/brain-typed-read-tokens.md) |
| Notifications, inbox, mobile push | [notification-inbox.md](notification-inbox.md), [mobile-push-contract.md](mobile-push-contract.md), [operators/mobile-push-delivery.md](operators/mobile-push-delivery.md), [operators/mobile-quick-action-preconditions.md](operators/mobile-quick-action-preconditions.md), [operators/human-gate-email.md](operators/human-gate-email.md) |

## Auth, identity and access

| I need to | Page |
|---|---|
| Central auth on the edge, logout, trusted proxies, step-up | [operators/central-auth-envoy.md](operators/central-auth-envoy.md), [operators/central-auth-logout.md](operators/central-auth-logout.md), [operators/trusted-proxies.md](operators/trusted-proxies.md), [../backend/docs/sso-step-up.md](../backend/docs/sso-step-up.md) |
| GitHub connection, SCIM groups, Cognito client cleanup | [operators/scm-github-oauth.md](operators/scm-github-oauth.md), [runbooks/scim-groups.md](runbooks/scim-groups.md), [operators/cognito-client-cleanup.md](operators/cognito-client-cleanup.md) |
| Cluster sign-in user preconditions | [operators/cluster-auth-user-preconditions.md](operators/cluster-auth-user-preconditions.md) |
| Environment, region and approval policies | [runbooks/operation-policies.md](runbooks/operation-policies.md) |
| Which permission gates what (reference) | [operators/identity-access-scopes.md](operators/identity-access-scopes.md), [operators/cluster-access-scopes.md](operators/cluster-access-scopes.md), [operators/lifecycle-access-scopes.md](operators/lifecycle-access-scopes.md), [operators/managed-resource-access-scopes.md](operators/managed-resource-access-scopes.md), [operators/observability-access-scopes.md](operators/observability-access-scopes.md), [operators/operations-access-scopes.md](operators/operations-access-scopes.md), [operators/registry-access-scopes.md](operators/registry-access-scopes.md), [operators/scm-access-scopes.md](operators/scm-access-scopes.md), [operators/form-access-scopes.md](operators/form-access-scopes.md), [operators/billing-access-scopes.md](operators/billing-access-scopes.md), [operators/pipeline-creation-access.md](operators/pipeline-creation-access.md), [operators/pipeline-secrets-access.md](operators/pipeline-secrets-access.md), [operators/workflow-import-access.md](operators/workflow-import-access.md), [operators/viewer-actions.md](operators/viewer-actions.md), [operators/legacy-core-access.md](operators/legacy-core-access.md) |
| Secret proposals, account anonymization, identity exports | [operators/secret-proposal-queues.md](operators/secret-proposal-queues.md), [operators/account-anonymization.md](operators/account-anonymization.md), [operators/identity-list-exports.md](operators/identity-list-exports.md) |
| Native app identity authority (GCP) | [operators/native-app-identity-authority.md](operators/native-app-identity-authority.md), [operators/gcp-prepared-identity-render.md](operators/gcp-prepared-identity-render.md) |

## Diagnostics and incidents

| I need to | Page |
|---|---|
| App unreachable, deploy stuck, control plane down, cluster degraded, webhooks failing | [runbooks/incident-response.md](runbooks/incident-response.md) |
| Control-plane metrics and diagnostics | [operators/control-plane-diagnostics.md](operators/control-plane-diagnostics.md), [operators/platform-activity.md](operators/platform-activity.md), [operators/frontend-query-freshness.md](operators/frontend-query-freshness.md) |
| Workload and ingress signals, topology traffic, network I/O | [operators/workload-golden-signals.md](operators/workload-golden-signals.md), [operators/ingress-metrics.md](operators/ingress-metrics.md), [operators/topology-traffic.md](operators/topology-traffic.md), [operators/app-network-metrics.md](operators/app-network-metrics.md), [operators/app-dependency-context.md](operators/app-dependency-context.md) |
| Logs and trace explorers | [operators/scoped-telemetry-explorers.md](operators/scoped-telemetry-explorers.md) |
| Install alert mail | [operators/install-alert-mail-ui.md](operators/install-alert-mail-ui.md), [operators/install-alert-mail-tests.md](operators/install-alert-mail-tests.md) |
| Support module (Client Cove) | [operators/client-cove-support.md](operators/client-cove-support.md) |

## Design, specs, history

- Design notes: [design/product-deployments-2465.md](design/product-deployments-2465.md), [design/agent-console.md](design/agent-console.md).
- Spec: [specs/multi-cloud-topology.md](specs/multi-cloud-topology.md).
- Retirements and journals kept for the record: [operators/retire-costing.md](operators/retire-costing.md), [operators/gcp-gke-preparation-journal.md](operators/gcp-gke-preparation-journal.md), [operators/gcp-workload-identity-journal.md](operators/gcp-workload-identity-journal.md), [research/](research/), [testing/](testing/).
- Sample manifests: [demo-apps/](demo-apps/), [examples/](examples/).
