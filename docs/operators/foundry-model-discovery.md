# Microsoft Foundry deployment discovery

`azure.foundry_catalogue.FoundryCatalogue` is an internal, read-only provider
adapter for existing deployments under an Azure public-cloud `AIServices`
account. It uses the installed `azure-mgmt-cognitiveservices` SDK and an admitted
Azure `TokenCredential`. It is not yet a public model-registration API or a UI
connection flow.

Before construction, the control plane must check the current organization,
credential, enabled provider and exact reviewed placement. Provider reads cannot
replace those checks. The adapter takes the subscription UUID, resource group,
account name and expected region from that admitted placement. It never accepts
an arbitrary management URL, credential material, continuation URL or caller
resource path. Azure routes the read to the declared subscription; this does
not verify the credential's Entra tenant or create a new account login.

## Read contract

- `deployments(limit=100, max_pages=5)` returns existing deployment metadata.
  Limits are 1-500 returned rows and 1-5 native pages. The SDK still receives a
  complete native page; the row limit is not a network response-byte limit.
- `deployment(name)` looks up one exact deployment name from the same account.
  URLs and resource paths are refused before a request.
- The account read verifies its native resource ID, name, region, resource type
  and `AIServices` kind. A second account read verifies that those facts and
  `disableLocalAuth` stayed unchanged during discovery.
- Every deployment must have the exact account resource prefix and native type.
  Details must match the requested name. Duplicate native IDs are refused.
- All transport calls use HTTPS GET on the exact reviewed ARM account/list/detail
  paths. Requests have a five-second connection timeout, ten-second read timeout,
  no retries and no redirects. Continuations that change host, account or path
  cannot reach the network. No keys are read and no deployment is changed.
- Results distinguish `complete`, `truncated`, `denied`, `not_found`,
  `invalid_identity` and `error`. A later-page failure invalidates the read;
  it never returns an empty successful inventory or retains partially trusted
  rows. `pages_read` counts successfully received native pages only.
- SKU/capacity and provisioning state are native metadata. They do not prove
  throughput, quota, runtime compatibility, model agreement or inference access.
  Every returned deployment has `inference_access="unknown"`.

The adapter returns no raw SDK diagnostics, response bodies, tokens or credentials
and writes no logs. Close it after the operation to release the underlying SDK
transport. Sovereign-cloud endpoint/audience admission is not implemented here.

## Connection and revocation boundary

Discovery does not adopt the deployment, claim lifecycle ownership, copy an
account API key, create app grants or permit deleting an externally owned model.
The existing Foundry lifecycle driver uses a shared account key. Removing an app's
local binding does not revoke a copied account key. The common Models UI and its
organization policies must use a separately proven connection/authentication and
reconciliation contract before exposing independently revocable subscriptions.

Native HTTP tests use the real Cognitive Services SDK and a controlled ARM
transport. They exercise decoding, account/deployment replacement, pagination,
continuation and redirect refusal, no-write enforcement, denied/error responses
and diagnostic-marker suppression. Those tests are not live Azure inference.

Microsoft's [deployment resource reference](https://learn.microsoft.com/azure/templates/microsoft.cognitiveservices/accounts/deployments)
documents the native resource hierarchy. Its [Foundry endpoint documentation](https://learn.microsoft.com/en-us/azure/ai-studio/ai-services/concepts/endpoints)
explains why a deployment's model metadata does not identify one universal client
protocol. The catalogue deliberately does not invent an inference URL.
