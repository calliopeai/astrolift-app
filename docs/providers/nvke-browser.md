# NVKE browser operations

The authenticated `/nvke` routes expose site registration, the four independently configured
interfaces, all actions returned by the pinned contract, cursor-paged operation history and
operation controls. `nvke.read` gates the routes; each action retains its own read, manage or
admin permission. Site and credential configuration require admin access. The backend checks
current authority again at admission and before remote I/O.

The native editor accepts JSON text without converting native integers through JavaScript's
numeric representation. Use the displayed schema and template to construct parameters. Review
the resource target before confirming a write. Operation admission does not prove readiness;
refresh operation details to observe status. An uncertain operation needs explicit reconciliation.

## Browser recovery

Native submission requires working site storage and the browser Web Locks API. Before sending,
the browser saves metadata scoped to the API server, organization, numeric actor and site:
request UUID, action, reviewed site version and upstream revision, timeout and SHA-256 of the
original parameter text. Native parameters, credentials and result contents are never stored
there or put in Apollo's normalized cache. Concurrent tabs serialize admission for the same scope.

A lost response retains this original identity. Retrying first checks the exact actor-bound
server receipt. A confirmed receipt opens its operation without another native submission.
If no receipt is visible, retry requires the identical original text, action and timeout and
reuses the original UUID and reviewed version. An absent receipt alone never proves no effect.
Copy the original input somewhere suitable before leaving an unresolved editor; the browser
intentionally cannot reconstruct it from storage.

Confirmed receipts are archived as metadata before the pending identity is cleared. A definitive
`INVALID_INPUT`, `VERSION_MISMATCH` or `SURFACE_UNAVAILABLE` admission rejection also archives the
identity before allowing corrected input. These codes cannot replace an existing original
receipt: the server returns that receipt before checking changed site versions or interfaces.
Authorization, source-change, conflict, internal and network failures retain the pending identity.
Storage failures block further admission rather than silently replacing a request UUID.

Changing the caller or organization clears the displayed forms and invalidates pending UI
callbacks. Reads are uncached and isolated by caller visit. An organization change while token
retrieval is pending blocks transmission; native admission additionally checks the reviewed actor.

## Results and current limits

The inventory API provides 18 resource collections derived from the pinned native contract.
`requestNvkeInventory` admits a read through the existing encrypted operation outbox; its
`isInventory` receipt distinguishes a complete collection from an ordinary native list call.
Inventory mode participates in idempotency without changing existing native request digests
or the 88-action registry. Callers must retain that mode when recovering a submission.

The worker follows native continuation tokens while preserving filters and checking current
authority, cancellation and deadline before each request. Duplicate identities, token cycles,
changed Kubernetes snapshot versions and malformed continuation metadata fail the observation.
Budgets of 100,000 resources, 2,000 pages and 48 MiB of encoded resource data fail explicitly;
no truncated collection is reported complete. Kubernetes collections carry a native resource
version; public/controller collections have no cross-page snapshot guarantee from upstream.

After success, `astroliftNvkeInventoryPage` searches the entire encrypted observation and
returns a server-paged metadata allowlist. Its cursors bind the organization, current actor,
operation version, collection, search and page size. Native numeric counts remain strings.
Condition summaries include at most 64 names/statuses and report `conditionsOmitted`; native
condition messages, specs, annotations and credentials require an explicit document request.
`astroliftNvkeInventoryDocumentText` returns exact JSON text for a signed resource key only
after rechecking current organization membership and the collection's native permission.
Observation timestamps describe historical reads and do not establish current readiness.

Results are fetched only after explicit confirmation and downloaded as JSON text, preserving
native integer values. Treat these files as sensitive: native responses may contain credentials
or kubeconfigs. No result is automatically fetched when opening an operation page.

This route-wiring slice uses manual refresh. Resource-specific inventory/detail views, site
bootstrap, workload access, telemetry and live hardware qualification remain separate delivery
work. The native editor alone does not establish full integration qualification.
