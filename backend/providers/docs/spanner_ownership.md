# Spanner Graph ownership and dedicated-container isolation

New dedicated instances use the managed-service UUID in their default physical
name. Mutable organization, app and environment slugs do not select a container.
Existing `graph_db/<instance>/<database>` handles retain both recorded names;
conflicting desired `instance_id` or `database_id` refuses the operation.

Dedicated instances carry `astrolift-managed-by=platform` and the immutable
`astrolift-organization-id` UUID. Databases retain their managed-service identity
in the protected ownership DDL. Status, bindings, snapshots, updates and teardown
check the exact returned instance/database identity and these ownership facts.
A platform marker alone does not authorize another organization's container;
tenant config and `force_destroy` cannot bypass ownership.

## Capacity and empty-instance deletion

The exact install-configured `shared_instance_id` permits separately owned
service databases across organizations. Its initial compute defaults come from
operator configuration. Tenant requests cannot resize it or delete it, including
requests with platform exclusivity proof or forced database destruction.

For a dedicated instance, capacity changes and empty-instance deletion require
both of these independent proofs:

- Central persisted inventory establishes that no other live or unreconciled
  Spanner service targets the same GCP project and instance. Soft deletion and
  `DEPROVISIONING` status do not prove cleanup. Failed records without handles
  still contest their desired target; unresolvable placement fails closed. The
  scan is bounded to 1,000 contenders and refuses if it cannot prove completion.
- The provider observes the complete database set, which must contain only the
  exact owned database (or be empty at the explicitly allowed creation/cleanup
  check). Inventory pagination is bounded to ten pages/10,000 records; malformed,
  repeated or unfinished pages refuse the operation. Instance deletion also
  requires no backups and rechecks emptiness and immutable ownership after
  dropping the database.

Platform provision, update and teardown run under a transaction, source/ancestry
row locks and a PostgreSQL advisory lock keyed by physical project/instance.
Fresh rows are rechecked after waiting, before constructing the driver and
credential-bearing configuration. The provider independently rechecks the
actual database set before resizing or deleting. These are platform concurrency
protections, not a Cloud Spanner transaction across instance/database APIs:
[the instance update API](https://docs.cloud.google.com/spanner/docs/reference/rest/v1/projects.instances/patch)
uses a field mask. Operators must prevent independent direct cloud writers from
racing platform ownership operations through their IAM/process controls.

## Observed placement and cleanup provenance

Migration `astrolift_services.0033_provider_cleanup_receipt` adds two nullable,
server-owned, non-editable internal JSON fields; neither is projected publicly.
Apply the additive migration before the new worker. Old rows and old insert
shapes remain nullable. No published migration is modified.

`provider_placement_identity` records the last successful observed physical
placement together with the recorded handle: service/organization/cluster/provider
UUIDs, driver, GCP project, instance, database and observation timestamp. Later
operations refuse changed placement before credential/config construction, and
repeat the comparison against freshly locked rows. A failed reprovision retains
the previous observation and timestamp; desired configuration cannot replace it.
This leaf provides no implicit resource relocation or tenant-editable recovery
mechanism. An operator must recover inconsistent metadata deliberately instead
of reusing an old handle against a different project or provider.

`provider_cleanup_receipt` records a successful provider teardown of that exact
incarnation. Only a soft-deleted row with a matching complete receipt may stop
contesting its previous container. Any next provision/update clears cleanup
before attempting the provider operation; it never clears observed placement.
A generic already-gone message coerced by the legacy lifecycle does not create a
receipt. Actual provider database inventory must still agree before another
service can resize or delete a container.

The dedicated fields are written only by the provider-confirmation helpers.
Manifest sync only writes typed lifecycle intent; GraphQL create/update/reprovision
inputs, desired config, restore metadata and retained-snapshot policy cannot set
these fields. Django admin forms exclude them. Caller-controlled lifecycle JSON
is never cleanup or placement authority.

A legacy instance missing its organization UUID can be backfilled only with an
exact recorded handle, exclusive service-handle and central container proofs,
a complete single-database cloud inventory and database ownership evidence.
Foreign owners and unknown/uncontested records cannot be adopted with tenant
flags. Legacy rows have no historical GCP-project provenance: these checks do
not reconstruct which historical project originally created an unpinned handle.
A successful operation pins the current placement actually observed; pre-existing
legacy project ambiguity requires operator reconciliation and is not claimed
resolved by labels or slug matching.
