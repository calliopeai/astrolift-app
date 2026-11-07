# NVKE browser operations

The authenticated `/nvke` routes expose site registration, the four independently configured
interfaces, all actions returned by the pinned contract, cursor-paged operation history and
operation controls. `nvke.read` gates the routes; each action retains its own read, manage or
admin permission. Site and credential configuration require admin access. The backend checks
current authority again at admission and before remote I/O.

`/nvke/{siteId}/resources` lists the pinned collection definitions (a fixed-cardinality
catalog, not a capped resource inventory). Each collection opens an inventory editor and
saved observation list. Running a read admits a complete inventory operation; the returned
snapshot UUID is kept in the URL. Refresh checks operation status and saved metadata without
starting another native read or discarding the same visit's native parameter draft. Admission
stays disabled while source data refreshes or has errors. Caller/site changes clear the view.
Successful inventory operations also link here from operation
details. Failed/pending observations never present a partial resource list.

Resource rows link to reloadable observation details using deterministic signed keys bound
to the snapshot version and row index. Detail reads return only the metadata allowlist;
the full native document remains an explicitly confirmed download. Collection, site,
snapshot and caller identities are checked before displaying data. Search and page cursors
remain server-side; native filters stay available as exact JSON text in the inventory editor.

The native editor accepts JSON text without converting native integers through JavaScript's
numeric representation. Use the displayed schema and template to construct parameters. Review
the resource target before confirming a write. Operation admission does not prove readiness;
refresh operation details to observe status. An uncertain operation needs explicit reconciliation.

## Browser recovery

Native submission requires working site storage and the browser Web Locks API. Before sending,
the browser saves metadata scoped to the API server, organization, numeric actor and site:
request UUID, action, reviewed site version and upstream revision, timeout and SHA-256 of the
original parameter text. Format-2 records bind native versus inventory mode; format-3
resource edits additionally bind the reviewed source operation, version and signed resource key.
Existing format-1 records remain native requests. All editors share the same journal and
cross-tab lock, and must resolve an outstanding request before changing its mode or source.
Receipt recovery checks both the explicit `isInventory` value and the resource source against
the original mode/binding.
Native parameters, credentials and result contents are never stored
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
Workload kubeconfig operations additionally enforce [native ownership proofs](nvke-access.md)
before reading the admin Secret; a supplied backing cluster name is not ownership evidence.
The [resource editor/admission API](nvke-resource-operations.md) supplies server-bound targets
and native preconditions for the existing-resource mutation forms on resource details.
Editors load sensitive writable configuration only after consent, retain opaque native JSON,
and confirm the fixed target and reviewed site version. Public interfaces prepare a native GET
when an ETag is absent; controller edits explicitly show their weaker concurrency guarantee.

Observation browsing uses manual refresh; a confirmed preparation GET polls its own operation
for a bounded interval. Collection creation, native subresource/lifecycle controls, site bootstrap,
additional workload access management, telemetry and live hardware qualification remain delivery work.
Observation browsing and the native editor do not establish full integration qualification.
