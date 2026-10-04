# Existing Bedrock model connections

The `models.bedrock_connections` capability advertises this API implementation.
It does not establish provider configuration, administrator authority, IAM access
or successful inference. The public `models.bedrock_connections_enabled` runtime
flag is off by default. Only a current active installation superadministrator
with the required administrator credential ceiling can change this model flag.
The Django setting is `BEDROCK_MODEL_CONNECTIONS_ENABLED`.

`bedrockModelConnectionSupport` admits opening the connection wizard using current
organization authority and the global feature. It performs no cloud reads and
does not authorize any cluster. The selected placement must independently pass
`bedrockModelConnectionAction`, current organization and cluster permissions,
provider enablement, cluster eligibility and reviewed cluster/provider versions.
Registration, editing and removal require current hosting authority, including
fresh persisted browser-session or bearer admission after blocking locks and
metadata reads. Ordinary application owners do not gain hosting authority.

## Exact existing source

`bedrockModelSources` returns bounded metadata with explicit partial and truncated
states; `bedrockModelSource` reviews one exact identity. Registration accepts only
on-demand foundation models or active inference profiles observed through the
declared AWS credential. The saved source pins organization, cluster, provider,
account, region, partition, credential declaration, model/profile ARN, source
fingerprint and the complete destination-model ARN set. A changed profile or
credential declaration refuses new grants instead of expanding them silently.

`registerBedrockModelConnection` records an existing connection. It does not call
the paid lifecycle driver, provision throughput, create invocation logs, tag the
cloud model or copy operator credentials into an application. Native model DTOs
carry `nativeSource.protocol = BEDROCK`; inapplicable local runtime fields are
null. Native `status = active` means the recorded configuration is current.
`ready` and `runtimeSupported` remain null and `invokeAccess` remains unknown.
Local Kubernetes density excludes native connections, and local deployment and
subscription serving metrics return unsupported observations for them.
The common `sourceKind` discriminator remains native when the global feature or
current credential declaration is unavailable; `nativeSource` is null when the
current declaration cannot validate the saved identity. This absence is not
evidence that the recorded source was deleted or that another source was adopted.

Migration `0037_native_bedrock_connection_owner` expands the owner constraint.
Reverting it fails while any Bedrock connection row remains, including a
soft-deleted row. Disable the feature and retain the forward schema instead;
never purge or recast native sources to force the old constraint to accept them.

## Application subscriptions and reconciliation

Application owners use the same organization/model AUTO, REQUIRE_APPROVAL or DENY
connection policy and shared/dedicated restrictions. Approval intake creates no
IAM or workload binding. Distinct eligible votes and the reviewed policy/target
versions are checked again at finalization. An approved request requires a
separate current-owner Connect action; it is not already connected.

The application and model must share the exact configured AWS cluster. A pending
subscription queues native reconciliation, not vLLM deployment. Named bindings
contain the exact model/profile ARN, region and SDK endpoint with `bedrock` API
style and `cloud_identity` authentication. There is no API key. Application code
must use AWS SigV4 credentials supplied by its workload identity.

Native roles require immutable organization, application and cluster GUID tags,
the exact declared AWS account/path/OIDC provider and only current coherent
ServiceAccount subjects. ServiceAccounts require matching GUID ownership and
role annotation. A legacy role or ServiceAccount without this proof refuses
before effects; an operator must resolve its ownership. Slugs, namespace names
or a platform-managed marker alone are insufficient. IAM calls use bounded
private transport and ignore ambient endpoint overrides.

Reconciliation computes a union across all coherent application environments.
It manages only the Astro-owned `astrolift-workload-policy` inline policy and
preserves external policies and grants. Removing the last native grant deletes
only that owned inline policy, not the role. Another policy may still permit
access, so an Astro subscription or its revocation does not prove exclusive
per-model isolation in AWS.

The desired subscription revision remains pending until IAM readback, protected
binding writes and current destination workload observations agree. An active
subscription means that configuration was observed; it is not an inference test.
The binding may require an application restart. Existing vLLM reconciliation and
credential-key behavior remain separate.

## Removal and unavailable sources

Revocation removes the owned named binding and obsolete managed grants even if
the selected source disappears, denies metadata reads or changes its destination
set. Current original credential/account/role ownership is still required. In a
mixed removal, invalid retained sources lose their obsolete managed grants and
remain failed without advancing their applied revision. Historical revoked rows
cannot admit a new connection under cleanup semantics.

`unregisterBedrockModelConnection` soft-deletes only the Astrolift record after all
subscription removals have been reconciled. Its synchronous success returns the
reviewed pre-deletion DTO; refresh the inventory to confirm absence. It never
deletes or modifies the external foundation model, inference profile or paid
throughput. Snapshot, native prompt testing, per-app token usage and cost are not
implemented by this connection path. No local test establishes live AWS invocation
or a deployed application identity.
