# Managed resource access scopes

Managed services check the live app that owns a private resource, or the live
project that owns a shared resource. App environments must still belong to
the named app. Projects and populated home-team ancestors must be live,
consistent and inside the active organization. Missing and stale targets take
an explicit organization scope; selecting a team or project supplies no
fallback authority.

A private resource targets its app environment's cluster. A shared resource
targets its own cluster. Deleted or foreign organization clusters are refused
before provider access, including for an organization operator; correct the
environment or service's cluster reference before using those actions. Platform
clusters without an organization retain their supported shared use.

Project resource creation checks the destination project. Attachment changes
check both the owning service and the app or agent recipe being attached.
The destination app check uses its persisted environment and cluster facts,
so a staging consumer cannot borrow an allowed production service's policy
context. Consumer checks finish before resource or attachment writes.

Secret bundles keep their recorded owner: project, team, or organization.
An app-scoped grant on one consumer does not authorize changing its project's
shared bundle. Bundle collections filter to these owners before their limit;
managed-service and model collections confine rows before pagination and
counts. An organization-only policy grant sees organization bundles without
revealing project or team descendants. Non-inheriting team and project grants
show only bundles at their exact owner. Existing operation-aware filters remain
in force.

A bearer credential retains its organization and optional team ceiling even
when its user has an organization role. App shares must delegate every actual
action on a gate: a viewer share can read a private service but cannot reveal
connections or update email resources. Project resources require the
credential's own live team. Organization bundles require an organization
credential.

The ten project-resource MCP tools use the same scoped targets and persisted
operation facts as their GraphQL mutations. A denied scope is returned through
the normal MCP `permission_denied` envelope and audit decision. The existing
MCP request schemas remain compatible. The additive GraphQL metadata
and reviewed-context contracts below require a matching server/client release.

## Metadata, consumers and reviewed runtime reads

Basic project triage uses `astroliftProjectManagedServicesPage`, independently
authorized `astroliftProjectManagedServiceAttachmentsPage`, and exact
`astroliftProjectManagedService(projectId, id)` reads. App-owned detail uses
`astroliftManagedService(id)`. An exact GUID never resolves a same-name
replacement. Pages bind their scope/filters into the cursor; removed anchors or
changed scope require restarting the page rather than guessing continuation.
Consumer counts reflect visibility, not all subscriptions in the installation.

Metadata exposes owner/placement GUIDs and versions, status, timestamps,
`contextRevision` and operation receipts. It excludes provider configuration,
connection material and unrequested grants. Pricing remains an explicit
independently permitted read with amount, currency, source, fetch time and
approximation evidence; missing evidence is unavailable rather than zero cost.

Attach, detach, update, reprovision and deprovision accept a nullable
`expectedContextRevision`. Reviewed clients submit the fresh metadata revision;
the server checks credentials and current policy and locks the service/parents
before writes. Reviewed worker operations also revalidate their operation token,
owner, placement and desired configuration before provider effects. Legacy
histories without proof retain their compatibility path. An accepted/enqueued
operation does not certify completion or provider convergence.

Workflow start and receipt annotation are separate. Once the engine accepts a
start, saving its run ID uses nonblocking locks on the reviewed service and its
parents. Contention, a replaced operation or an annotation failure leaves the
run ID unconfirmed; it does not convert an accepted start into a failed one.
Worker operations retain their blocking safety fences. Enqueue on transaction
commit is not a durable dispatch outbox.

For app-owned generic exporter metrics, pass that same revision to
`astroliftAppManagedServiceMetrics(managedServiceId, expectedContextRevision)`.
The server checks current placement and authority, reads the captured cluster
endpoint directly, and refuses samples if context changes during collection.
These exporter charts do not certify physical workload CPU/memory identity; see
[workload measurement scope](./workload-golden-signals.md).

Web clients clear resource metadata, cursors, samples and captured handlers on
actor/organization/context changes. Runtime requests are no-cache and disable
in-flight deduplication, including when switching back to a prior actor. Missing
or refused context cannot mount runtime charts or enable writes.
