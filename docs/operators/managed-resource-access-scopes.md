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
counts. Existing operation-aware filters remain in force.

A bearer credential retains its organization and optional team ceiling even
when its user has an organization role. App shares must delegate every actual
action on a gate: a viewer share can read a private service but cannot reveal
connections or update email resources. Project resources require the
credential's own live team. Organization bundles require an organization
credential.

The ten project-resource MCP tools use the same scoped targets and persisted
operation facts as their GraphQL mutations. A denied scope is returned through
the normal MCP `permission_denied` envelope and audit decision. Tool schemas,
GraphQL arguments and response shapes remain unchanged.
