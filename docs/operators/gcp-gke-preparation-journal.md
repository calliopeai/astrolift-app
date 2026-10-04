# Private GKE preparation journal

This is a backend durability adapter for the private GCP preparation port. It
does not register a public workflow, create a GSA, grant IAM access, deploy an
app, or prove impersonation, inference or rollout readiness. Full #2278 caller
orchestration and workload acceptance remain open.

## Original identity and accepted operations

`PreparationTarget` pins the original organization, app, cluster and provider
GUIDs; declared credential SHA; native project ID/number and region; GKE
resource and immutable `Cluster.id`; original GSA ID/unique ID; and physical
namespace/KSA names with their initial UID ceiling. Its original alias metadata
is historical. An original representative environment may retire without
changing the physical identity or adopting a replacement UID.

Each protected `GCPGKEPreparationOperation` retains its own operation UUID,
generation, nonce, revision, workflow/execution identity, canonical schema-2
`AcceptedPreparationTemplate`, and exact signed `AcceptedAppIdentityAuthority`
metadata payload and SHA. The reference contains no plaintext bearer or browser
session key. Parsing and matching its SHA do not authenticate a caller: the
checkpoint must re-admit the original reference with
`current_app_identity_authority` and evaluate the complete current accepted
source/alias snapshot, including every alias's actual ABAC operation facts.
Only an actual server HTTP boundary may originally capture that reference.

The logical template groups all current environment GUIDs sharing a physical
namespace/KSA. It retains every alias rather than inventing additional objects.
Current alias membership is per operation. The IAM journal's
`KSAIdentity.environment_id` remains the original representative GUID for that
physical UID; it is not a current environment authorization list. New physical
subjects or replacement UIDs require a separately reviewed evolution contract.

`accepted_union_template_sha256` binds the server-produced logical grant/alias
plan and source snapshot before UIDs exist. `bind_native_union` uses that stored
plan and actual committed original KSA UIDs to bind exactly one
`derived_native_union_sha256`, using the native port's pure grant planner.
These hashes are deliberately different. A supplied opaque SHA cannot replace
the stored logical grant plan or fabricate UIDs.

## Transaction and transport boundaries

Call `preparation_journal_mutex(target)` outside any transaction. Its dedicated
autocommit PostgreSQL connection only holds an advisory lock, anchored to the
original app/cluster regardless of later organization/provider reassignment.
It does not acquire FK or business-row locks. The caller must also hold the
nonblocking IAM mutex for joint operations, releasing either on acquisition
failure. No lease or missing worker permits takeover.

Every adapter method uses a short committed transaction and a mandatory
checkpoint returning exactly `None`. Parent row order is organization,
cluster/provider, team/project, app, then IAM journal before preparation
journal/operation when both are involved. All row locks use `NOWAIT`; a genuine
PostgreSQL 55P03 collision rolls back and returns `JOURNAL_BUSY`. This allows an
existing app/environment-to-cluster writer to continue. Current environment
aliases are freshly read rather than row locked. The source checkpoint must
compare their complete current snapshot after the last journal lock. This is
current admission, not a globally atomic authority snapshot. The IAM storage
dependency must include its matching NOWAIT correction for
`completed_configuration`; the historical initial IAM journal alone still has
blocking parent locks. A busy result requires fresh current re-admission, not a
blind continuation or native resend.

Native transport runs after those transactions commit. Wire, credential and
source checkpoints run outside the transactions; no cloud read belongs inside
the storage callback. Native discovery/refresh must use the separately bounded
source port. The storage adapter itself never discovers ADC or opens a cloud
client. Pure validators close their port objects.

`commit_submission` writes exact `UNSENT` then `SENT` intent/ledger records and
returns matching `PreparationCommitReceipt` values only after commit. A boolean
callback is not durability proof. `commit_observation` accepts a new unpinned
UID only from its exact recorded SENT create and original operation marker.
UNSENT observations cannot adopt independently appearing objects. Original UID
changes, foreign paths, changed request bytes or partial ledger substitutions
refuse. Observed UID/resourceVersion records commit before IAM consumption.

The create intent distinguishes the canonical pre-marker request digest from
the actual wire-body digest including the marker. The native marker plus GUID
labels are not permission to adopt an arbitrary preexisting object. Originally
pinned UID observations and exact accepted SENT recovery are distinct paths.

The `PREPARE` result reaches `PREPARED`, never terminal `OBSERVED`. Partial,
lost-write, lost-commit-acknowledgement and unknown results retain their original
operation and obligations. A stored SENT/UNKNOWN cannot be reset into UNSENT,
silently erased, advanced to another accepted plan, or blindly resent. A lost
acknowledgement requires reloading actual committed state, not trusting the
error's last acknowledged in-memory ledger. A known original native observation
may resolve only that exact operation; it is not an exclusive native lock or
proof that an older external RPC was cancelled.

## Annotation and later connect/detach plans

`verified_annotation_receipt` reads the actual current completed IAM journal,
then locks/revalidates that same journal before preparation. It requires the
same original identity, operation, authority hash, derived union, committed UID
set, current reservation fence, completed desired revision and empty pending /
removal obligations. An arbitrary `VerifiedIAMConfigurationReceipt` echo is not
accepted by this adapter. PATCH submissions, their observation commits and final
annotation completion recheck the actual IAM row under the same fence. The
annotation port's per-native-operation checkpoint must also call
`validate_current(..., iam=held_iam_binding)` to re-admit that completed fence.
Current browser logout, revoked/expired session metadata and original bearer
withdrawal refuse through the original-credential authority service; parsing
accepted metadata never grants authority.

The unchanged provider port tests the original UID, current opaque resource
version and existing annotations before adding the GSA link. It preserves
foreign labels/annotations and refuses a competing link. Only a successful
`ANNOTATE` result with current completed IAM evidence reaches terminal
`OBSERVED`; that records configuration, not pod or credential readiness.

A strictly newer accepted plan requires the preceding preparation operation
terminal and its actual IAM journal still fully completed at the recorded
previous fence. Old accepted templates and observed UIDs remain protected.
The new plan receives fresh current admission; the old source plan is not
required to remain current merely to inspect its completed fence. This permits
connect/detach or alias membership changes for the original physical subjects.
Changing the plan under the same UUID refuses. A current source withdrawal or
uncertain old operation cannot be converted into permission for a new plan.

An empty **Endpoint grant union** removes owned Endpoint prediction grants
while preserving the original GSA/KSA impersonation grants and configured link
for retained physical subjects. It is not an empty full-IAM union or identity
decommission. Native IAM cleanup preserves foreign/conditional policies and
does not imply access is denied when another grant allows it. Mixed-resource
app unions remain unsupported until their precise IAM ports are implemented.

## Retained history and rollback

Migration `0039_gcp_gke_preparation_journal` depends on the actual `0038` IAM
journal migration. Both original journals and accepted operation history use
protected tenant FKs, GUID/tracking fields and soft-delete metadata. The private
adapter refuses any retired history at the stable app/cluster anchor. Accepted
metadata and a bound native union cannot be changed through the operation's
`save`, and product retirement/erasure has no supported path.

Reversal refuses **any** retained journal or operation row, including retired
ones. Disable future callers and retain the forward schema; deleting history
to make a rollback pass is not an operator recovery procedure. Privileged SQL
or a database administrator remains outside these application protections.
