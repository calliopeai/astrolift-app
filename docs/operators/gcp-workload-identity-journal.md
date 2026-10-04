# GCP workload identity journal foundation

The private `GCPWorkloadIdentityJournal` and `journal_mutex` store committed
ownership metadata for the owned GCP IAM port. They do not wire app deployment,
create a GSA or KSA, configure GKE, authorize a request, or prove token exchange,
Vertex invocation or workload readiness. There is no public journal API.

One live row is anchored by the original app and physical cluster, independently
of current organization/provider assignment. The organization and provider are
immutable protected owner fields; replacing either cannot open an empty ledger.
These parents use protected foreign keys. The original project ID and number,
GSA numeric identity, GKE native identity, observed namespace/KSA UIDs and
credential-declaration digest form an immutable target snapshot. An accepted
operation binds its UUID, workflow/execution IDs, desired revision, desired
union digest and authority-reference digest. Digests identify accepted metadata;
they are never grants. No token, session key, policy body, native error or cloud
response belongs in the journal.

## Committed write protocol

1. Refuse an enclosing Django transaction or disabled autocommit. Acquire a
   nonblocking session advisory mutex on a separate PostgreSQL connection. This
   connection only locks; it never reads or writes parent/journal rows.
2. In the normal Django connection, take short canonical parent-first locks:
   organization, cluster, provider, current team/project, app, journal. Recheck
   coherent ownership and invoke the mandatory current-authority/source callback
   after the final lock. Reserve the original operation and commit.
3. The native port supplies a typed UNSENT submission. Persist its exact
   resource, before/after/etag and desired-union hashes plus bounded owned grants;
   return the committed journal GUID/version and ledger digest.
4. After fresh native source/role checks, commit the exact SENT phase. Only a
   successfully returned durable receipt permits the port's single no-retry
   setter. No database lock is held during native calls.
5. Preserve SENT/UNKNOWN on a lost response or uncertain commit. A before-hash
   read, expired mutex owner or missing heartbeat does not prove cancellation.
   There is no lease takeover, automatic resend or retarget operation.
6. The native port can resolve its own SENT pending intent after exact after-hash
   observation. The store verifies that the resulting ownership matches that
   recorded intent. A complete reconciliation receipt records the observed
   revision only after pending/removal obligations are empty. This remains a
   supplied native observation, not independent cloud verification by storage.
   `record_result` defaults to incomplete; only a successful complete-union
   `NativeIdentityResult` may supply `completed=True`. An error receipt with all
   attempted steps observed still does not confirm the final union.

`completed_configuration` re-reads the actual current committed row under the
same mutex, reservation and fresh checkpoint. It returns immutable metadata
only for the completed desired revision with no pending/removal obligations and
all submissions observed. Its pinned target includes the original GSA, native
GKE identity, provider and namespace/KSA UID set. A constructed result object is
not authority; later annotation must recheck this actual journal and the current
admitted operation throughout its own native calls.

Use the provider's schema-version-2 `OwnedGrantLedger` codec. Resource and grant
entries are bounded and constrained to the recorded project/region, GSA and
observed KSA principals. New submission phases must enter through
`commit_submission`; `persist_ledger` cannot manufacture an UNSENT/SENT phase.
An UNSENT native after-hash coincidence cannot establish owned grants. The
caller must retain the last acknowledged receipt on uncertain persistence;
a failed acknowledgement is not permission to send or replace the journal.

## Integration and recovery limits

The integration must not introduce model-row locks after the cluster lock.
Current admitted snapshots/checks must preserve the sequential, non-atomic
native boundaries rather than invert existing model/cluster/app writer locks.
The integration must reconstruct the complete coherent app-on-cluster union,
including every current environment/binding, and use the accepted-authority
reference consumer to re-admit the original actor and credential after waits.
The checkpoint must also compare actual current provider/configuration,
GSA/GKE/KSA identity, union and accepted context; a callback that merely returns
`None` is not authority evidence. These consumers and production orchestration
remain separate work. Background origin IP is not invented.

The target snapshot conservatively pins the original observed KSA set. Adding
or replacing that set requires a separately reviewed recovery/migration design;
this foundation does not silently substitute a new UID or retire an owned
journal. Soft retirement/restore is refused, and a previously retired row blocks
silent replacement. Existing owned grants survive later desired revisions.
Operator repair for unresolved native sends is not implemented by this table.
A cleared database pending entry does not establish that another remote sender
has stopped; orchestration must continue to hold the mutex and current fences.

Focused tests use real PostgreSQL transactions and independent connections.
They perform no cloud IAM write, KSA rollout or invocation.

## Migration rollback

Unpublished migration `0038_gcp_workload_identity_journal` refuses reversal when
any journal row remains, including retired or observed rows. Dropping the table
would erase ownership and uncertain-send evidence. Empty disposable databases
can migrate down and reapply. This reviewer-visible constraint does not provide
an operator repair path or permission to delete retained history for rollback.
