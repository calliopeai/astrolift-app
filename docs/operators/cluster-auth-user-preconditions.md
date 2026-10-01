# Reviewing cluster sign-in user targets

The authenticated `astroliftClusterAuthUsers` inventory exposes optional
`source { providerPluginId, providerPoolId, sourceVersion }` and each user's
optional `providerUserId`. These are target observations, not authorization.
Cognito uses its literal user-pool ID and immutable `sub`, never email or a
username inferred from a display label. Passwords and cloud credential values
are absent from these reads and from the source-token payload.

Pass the observed `source` as `expectedSource` to all seven existing mutations:
create user, create group, set password, reset password, enable/disable, delete,
and update memberships. Existing-user writes additionally require the observed
`providerUserId` as `expectedUserId` when using `expectedSource`. Supplying only
`expectedUserId` is a validation refusal. Missing or mismatched proof returns the
existing structured `MutationResult` error envelope before any provider write.
The connected web UI sends these preconditions and disables reviewed actions
when source or applicable user proof is unknown. It preserves literal IDs and
retains its client source leases for observed stale dialogs and late responses.

`sourceVersion` is an opaque keyed proof of the cluster/provider GUIDs and
existing tracking revisions/timestamps, source availability and dispatch
metadata. It does not encode credential values. Normal configuration saves,
including A→B→A, invalidate prior reviews. The cluster and provider models persist
`version` and `updated_at` on partial source-field saves, including credential
refresh and registration `update_or_create` paths. Unrelated tracking changes
can also invalidate a review; refresh and review again rather than retrying the
old proof. Key rotation likewise invalidates proofs. Maintenance scripts must
use tracked saves; direct SQL/bulk updates bypassing revision maintenance cannot
provide configuration ABA detection.

Before effects, the backend locks and reloads the current cluster and provider,
checks source availability and the reviewed proof, and refreshes current
`CLUSTER_USERS` authority against the locked organization/region. Existing org
scope, bearer ceilings and the platform-operator requirement for install-shared
pools remain authoritative. Inventory rechecks persisted source markers after
provider reads and discards a source changed during the read. It still returns
the provider's bounded subset, not a complete pool census.

Cognito's optional reviewed-driver extension verifies the current
`AdminGetUser` subject immediately before each existing-user effect and rechecks
current actor/grants before each SDK write. Group changes recheck before each
membership effect. User creation verifies the subject returned by
`AdminCreateUser` before permanent-password and group follow-ups in a reviewed operation.
The scoped provider role needs `cognito-idp:AdminGetUser` on the exact configured
pool for these reviewed reads, in addition to the existing relevant admin write
actions. This change does not grant IAM permission automatically. Creation never
adopts an existing username. Unknown subjects refuse follow-ups.

## Compatibility and actual race boundary

The new read fields and input fields are additive and nullable. Callers that omit
both expectations keep the existing current-target semantics, resolving the
currently configured pool and supplied username. They do not gain an immutable
review guarantee. Legacy creation keeps its prior SDK effect sequence and does
not add an `AdminGetUser` requirement before password/group follow-ups. Existing
drivers remain compatible; reviewed operation support
is an optional protocol extension. A driver without authoritative proof leaves
that proof unknown and the reviewed UI read-only.

Cognito admin writes accept `UserPoolId` and `Username`, without an expected-sub
or version condition. A provider-side delete/recreate between `AdminGetUser` and
an admin write can still affect the replacement username. PostgreSQL locks do
not serialize cloud-console or other external Cognito writers. No provider
compare-and-swap, exactly-once effect or external atomicity is claimed.

Multi-call operations can partially complete: a user may be created before a
password/group follow-up is refused, or an earlier membership update can succeed
before a later identity/authority check fails. A provider transport failure can
also leave effect status unknown. Database rollback does not undo Cognito
writes. Refresh the actual provider inventory and review the remaining work;
never treat a refused envelope as proof that all preceding cloud effects were
reverted. Invitations/reset requests are accepted requests, not delivery proof.

No database migration is required. Rolling back removes these guards; legacy
current-target behavior resumes. Clients sending the new fields require a
matching schema, so roll back connected web/backend together. No production pool
changes, cloud provisioning or live user writes are part of local verification.
