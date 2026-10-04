# Bedrock native metadata reads

`aws.bedrock_catalogue.BedrockCatalogue` is an internal provider foundation for
#2269. It is separate from driver-variant discovery, paid lifecycle operations,
organization-owned connection registration and application access reconciliation.
No public API or plugin capability is enabled by this module.

The caller must admit the current actor, organization, provider, credential and
source version before construction and before releasing a result. A catalogue
listing is neither a registered connection nor permission to subscribe or invoke.

## Identity and sources

`BedrockCatalogueConfig` requires a region and an AWS `CloudCredential` with an
explicit twelve-digit declared account. Production uses the existing registered
AWS session factory; it never accepts a caller endpoint URL. The same private
session supplies STS and Bedrock. Every metadata request verifies actual STS
account, partition, caller ARN and principal ID before and after transport;
assumed-role mode additionally requires the configured role/session identity.
Each client has five-second connect, twenty-second read and one-attempt bounds.

Foundation identities must have an empty account component, the selected region
and partition, and matching native model ID/ARN. Profile identities must belong to
the selected account/region/partition and retain their system/application source
kind. Profile destinations preserve exact foundation-model ARNs across regions
within that partition. No profile is silently replaced by a foundation model.
Custom/imported models, provisioned throughput and marketplace endpoints are
outside this catalogue contract.

## Internal methods and observations

| Method | Bounded read | Result |
| --- | --- | --- |
| `foundation_models(limit=100)` | One `ListFoundationModels`; output limit 1–500 | Frozen page with explicit truncation. AWS supplies no native pagination for this operation. |
| `inference_profiles(limit=100, max_pages=5)` | `ListInferenceProfiles`, at most five calls/500 output rows | Native continuation stays internal. No caller token, offset-page or stable ordering claim. |
| `detail(kind, identifier, check_availability=False)` | Exact `GetFoundationModel` or `GetInferenceProfile` | Frozen detail tied to the requested ID/ARN. URLs, foreign identities and wildcard selectors are refused. |

Pages distinguish complete metadata, bounded/truncated metadata, provider failure
with partial admitted rows, and refusal. Identity-invalid rows or principal changes
discard the entire result, including earlier pages. Fixed denial/not-found/error
reasons omit raw provider diagnostics and continuation tokens.

Optional foundation availability calls `GetFoundationModelAvailability`. Native
authorization, entitlement, agreement and region observations remain literal;
`invoke_access` always remains `unknown`. Denied/unavailable/unknown availability
does not discard otherwise readable metadata. Profile metadata has no equivalent
entitlement guarantee, and an ACTIVE profile is not proof this principal or an app
can invoke it. No invocation, allocation, tagging, log-group write or grant change
is performed.

Operators need the relevant Bedrock List/Get read actions and a responding STS
identity path. This leaf attaches no IAM policy. Native boto3/Stubber tests validate
request and response schemas, bounded pagination and refusal behavior; they do
not prove live AWS access, paid capacity, inference or app binding.

Primary API references:
[ListFoundationModels](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListFoundationModels.html),
[ListInferenceProfiles](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_ListInferenceProfiles.html),
[GetInferenceProfile](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetInferenceProfile.html),
[GetFoundationModelAvailability](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetFoundationModelAvailability.html).
