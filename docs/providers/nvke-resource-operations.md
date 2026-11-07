# Resource-bound NVKE operations

The resource editor/admission API binds a native action to a successful resource
observation. It complements the full native workbench; it does not change the
88-action registry or the digests of existing ordinary/inventory requests.

`astroliftNvkeResourceEditor` takes the site, current actor, action and reviewed
source operation/version. An inventory source also supplies its signed resource
key. A successful single-resource native GET is an alternative source and does
not take an inventory key. The query reads encrypted observations only; it never
contacts NVKE or admits work. Its returned native parameter text can contain
sensitive configuration and must be loaded explicitly and kept out of persistent
browser storage and normalized caches.

The server projects writable fields from the pinned request schemas. Datastore
replacement documents retain their spec, labels and annotations, while excluding
status and server-owned metadata. Public replacements use public writable metadata
and spec. Controller patches retain fields present in the corresponding native
update schema. Operators may edit native JSON text without a JavaScript numeric
roundtrip; the server revalidates the complete submitted document. Native schema,
immutability and CEL rules still apply (for example, MachineImage spec immutability).

`requestNvkeResourceOperation` adds a durable outbox request using exact JSON text.
Its reviewed source participates in idempotency alongside the original submitted
parameters. Native targets and concurrency fields are supplied by the server;
conflicting caller path/name/UID/version values are refused. The encrypted
executable input contains the final native parameters. The immutable operation
authority retains the reviewed source and HMACs of identity/baseline observations,
without copying native documents into workflow metadata.

At admission, the source must belong to the same organization/site, still be the
reviewed successful version, retain the same interface identity/digest, namespace
and upstream revision, and permit the selected resource action. A native GET's
returned identity must agree with its original requested target. The current
action permission and actor are checked before parsing submitted native JSON;
source read permissions are checked before decrypting the observation.
The source is locked and its successful version rechecked before insertion. Admission
also refuses a new resource-bound mutation while another bound mutation for that
same site/resource identity is unresolved, including `uncertain` effects. The
original request ID remains recoverable; observation or explicit reconciliation is
required before another mutation. Reads remain available for that reconciliation.

For datastore mutations, the observed UID and complete opaque resource version
are fixed into the request. Replacement now supports an optional native UID and
carries it in the Kubernetes PUT body; delete/deprecate retain their existing UID
preconditions. Worker preflight checks identity/version, and Kubernetes enforces
the submitted preconditions at the write, including a replacement racing after
preflight. Existing callers may still use ordinary replacement without the new
optional UID.

Public list projections omit per-resource ETags. Their editor therefore returns a
preparation GET action until a successful resource read supplies the native ETag.
The prepared edit fixes that exact value into `If-Match`. It never invents a
version, substitutes the newest version on conflict, or falls back to datastore
credentials. Reads prepared from a saved inventory verify identity after the read
and may produce a fresh version without changing the inventory snapshot.

Controller mutations expose `observed_only` concurrency. They have stable UUID
targets and compare writable configuration with the reviewed observation before
dispatch. Heartbeats, readiness conditions and other read-only status changes do
not create false write conflicts. The pinned controller has no conditional-write mechanism, so
another writer can still race after that check. This limitation must remain
visible in the mutation UI; the platform cannot promise atomic controller updates.
Versioned interfaces expose `native_version` concurrency instead.

Receipts expose `resourceSource` separately from `isInventory`. The existing
native/inventory browser recovery path refuses to adopt a resource-bound receipt
as an ordinary request. Resource forms must extend that shared journal with the
source binding before submitting these operations; they must not create a second
nonce over an unresolved request. Existing confirmed receipts are returned before
rechecking changed/deleted source observations, preserving request recovery.

Local proof covers real PostgreSQL/GraphQL admission and real Kubernetes
replacement, stale snapshots, and replacement between preflight and PUT. Resource
form wiring, resource-mode browser recovery, collection creation flows, native
subresource/lifecycle controls and CLI source-binding flags remain delivery work.
