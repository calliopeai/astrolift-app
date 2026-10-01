# Account anonymization and attributed history

On an admitted first cleanup, `astroliftAnonymizeUser` keeps the user's structural
row, replaces the existing account/profile PII, disables the account and
deactivates every live membership at every scope. Retained role bindings remain recorded;
they cannot authenticate an inactive owner. Self-anonymization returns
`requiresLogout: true`; another person's cleanup does not log out the operator.
The legacy elevated account-deletion route performs its existing self-logout.
The existing tenant, grant-ceiling, fresh-elevation, protected-operator and
last-owner admission rules apply before cleanup.

`AstroliftUser.isAnonymized` is a nullable read-only indication of the stored
account state, not permission to erase someone. The shared predicate requires
an inactive user and an exact, case-sensitive `@anon-astrolift.net` email suffix.
Suspension, deactivated membership and lookalike domains do not imply erasure.
Normal user projections supply true or false; constructors without authoritative
state default to null. Member reads retain their existing permission and tenant
boundaries.

## Supported persisted reductions

| Source                          | Authority and changed fields                                                                                                                                                                                 | Preserved facts                                                                                     |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------- |
| Django User / core Profile      | Existing `Profile.anonymize_user` account/profile fields; random anonymous identity, unusable password, inactive account                                                                                     | User identity and structural references; profile anonymization follows its existing behavior        |
| operations Event                | Typed user resource identity or explicit recognized user identity within the payload; supported identity/contact/session PII string values in `payload`                                                      | Actor FK, organization, resource identity, event type, severity, request/trace IDs and timestamps   |
| operations AuditEvent           | Exact `actor_kind=user` plus actor PK for actor display, request IP/agent and actor/request PII; typed target-user identity for target slug and subject PII; explicit payload identities for nested subjects | Actor/target IDs and kinds, decision, action, organization, request ID, parent chain and timestamps |
| auth1 UserInfo                  | Exact `internal_user` FK: names, nickname, email, verification flag and picture                                                                                                                              | Issuer, subject and other opaque identity/session claims, FK and timestamps                         |
| identity AstroliftSession       | Exact user FK, including soft-deleted rows: label, last-seen IP/agent                                                                                                                                        | Session identity, client kind, timing, revocation and attestation facts                             |
| identity ApiToken               | Exact owner FK, including soft-deleted rows: last-used IP/agent                                                                                                                                              | Token hash, ID, scopes, expiry and revocation facts; inactive-owner verification refuses the token  |
| operations NotificationDelivery | Exact recipient-user FK, email/SMS only: target address                                                                                                                                                      | Delivery identity, channel, status, provider message ID, times and mixed content                    |
| core MutationAuditLog           | Exact actor-user FK: request IP                                                                                                                                                                              | Organization, actor FK, operation, variables, errors, success and timestamp                         |

The database projection supports direct string PII values and string leaves in
`{old, new}`-only value objects. Its identity fields include email, names,
username, phone, avatar/picture, address, birth date, session IP/agent, device
label and geographic hint, plus explicitly named actor/request/subject contact
fields. A non-string scalar or an arbitrary structured value remains unchanged.
Recognized actor/request and user/subject/target/profile/identity/before/after
containers carry only their attributable context. Explicit foreign, conflicting,
malformed or unknown identities stop inherited subject attribution. Other
users' branches and unknown nested fields are preserved. Being an event's
actor alone does not establish ownership of generic email/name fields.

An already-anonymous user can receive this cleanup, including supported history
imported after the first request, without replacing the anonymous username or
email or changing the profile anonymization time. A clean repeat performs no
privacy updates and returns `requiresLogout: false`. Repeat historical cleanup
does not perform another membership or authentication-group transition. The nullable status field
does not promise that every possible historical or external copy was erased.

## Append-only boundary and failure

Spec 04 §11 permits user-PII erasure while keeping references; §13 criterion 5
requires storage-level append-only event/audit/deployment logs. Migration 0023's
retention exception remains DELETE-only through the existing local retention
gate. Migration 0028 adds a separate, narrow privacy UPDATE exception for Event
and AuditEvent: the database requires an existing already-anonymous inactive
account and compares every column of NEW with OLD plus its own exact attributed
PII projection. A caller-set session flag cannot admit an active, ordinarily
disabled, nonexistent or lookalike target, or edit a decision, identifier,
timestamp, foreign subject or arbitrary payload. DeploymentLog updates remain
refused. Ordinary UPDATE/DELETE remain refused.

Initial account/profile writes and all supported history/cache reductions run
in one atomic transaction. A history failure rolls back the account/profile
transition and membership changes. The privacy gate is cleared on success and
exception, and is also transaction-local. Cleanup uses database-filtered updates,
not an all-history Python materialization. The post-call mutation-audit writer
does not reinsert an anonymous self's request IP; its successful anonymization
record contains structural identifiers rather than old names or email.

The IdP callback preserves an existing subject's linked user, requires the same
known issuer, and locks/rechecks the account before locking/updating its cache.
Inactive linked or email-resolved users are refused before cache/name updates,
login, group sync or auto-join. A known subject's former email cannot unlink an
anonymous account and enter auto-signup. Missing or changed issuer information
on an existing cached subject fails closed; this does not delete an external
IdP account. Existing verified active/new-user flows retain their provider
fallbacks and auto-signup policy. Unverified claims are refused before writes.

## Persisted and external exclusions

These copies can contain PII but have no supported blanket-erasure guarantee:

- Unknown, conflicting, opaque, narrative or freeform Event/AuditEvent JSON;
  arbitrary structured values even beneath a supported PII key; structural
  parent chains and another person's attributed fields.
- MutationAuditLog variables/errors, core GQLLog query/variables/request bodies,
  PermissionAccessLog descriptions and PinTransaction mixed data. Actor
  authorship does not establish every payload subject's ownership.
- DeploymentLog messages/details, workflow and Temporal history/results/failures,
  AgentTaskEvent text/request/data and SCM/webhook content or delivery excerpts.
  Shared SCM credentials and external provider content are not solely owned PII.
- NotificationDelivery excerpts/errors, shared webhook destinations, device
  attestation claims and device-flow client/approval metadata whose approval actor
  need not be the originating device's subject.
- Email-only invitations without an accepted-user FK, optional product-domain
  timelines and mixed tenant application data. Email matching is not sufficient
  identity authority; Django email addresses are not globally unique.
- Existing AuditArchive objects, AuditExport files, backups, downloaded copies,
  outbound webhook copies, telemetry and external IdP/search systems. Their
  retention/deletion procedures remain separate; cleanup does not rewrite archive
  hashes or claim external deletion.

## Release and rollback

Apply operations migration 0028 before running the new cleanup path. It installs
functions/guards; it does not scan or anonymize existing users on migration.
Verify the account-privacy PostgreSQL/API tests, existing tenant/owner/operator
admission tests and elevated HTTP self-deletion test against the release schema.
The concurrent callback test proves that a callback waits for erasure's account
lock and refuses to restore the cache after erasure commits.

Rolling back 0028 restores the released retention-only guard and removes the
privacy functions. Roll back the caller code with the migration: new cleanup
code refuses when its function is unavailable. Previously erased PII cannot be
restored by rollback. Operator review of this documented exception and supported
source inventory is required before a release can claim #2220 acceptance;
local proof is not production privacy execution.

## Connected People review

The People list requests the nullable `AstroliftUser.isAnonymized` field. Only
`true` offers **Review privacy cleanup** for an existing anonymous account;
`false` or unknown keeps the ordinary first-cleanup review. Member lifecycle
remains `deactivated`. Email, display name and membership state do not establish
anonymous status in the browser. Both actions call the same server-authorized
mutation with the exact user GUID and require irreversible acknowledgement.

All eight locale reviews describe the supported attributed-history reduction,
retained account/audit/role-binding facts and unsupported opaque, structured,
unattributed and external copies. Repeated cleanup preserves anonymous identity;
it does not claim blanket historical deletion or replace that identity.
Changing the reviewed GUID/name, list question or existing management visibility
withdraws the review. A cached same-target read failure preserves the review;
a confirmed missing row does not. Locale changes alone retain acknowledgement.
Accepted cleanup remains accepted if refresh or required sign-out navigation
fails, with separate recovery feedback. Navigation failure never claims the
browser session was successfully ended.
