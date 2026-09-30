# Preview names and workload identity

Preview creation preserves the usual `preview-<branch>` and `preview-pr-<number>`
environment names when free. If another live environment of the app already
uses that name, automatic naming adds a deterministic eight-character hash.
Both the manual mutation and PR webhook lock the app before checking
idempotency, selecting a name and namespace, and inserting the two rows.
Concurrent repeats therefore reuse one preview; concurrent manual branch
`pr-3` and PR #3 creation produces two distinct names and namespaces (#2095).

An explicitly requested manual `environmentName` that is occupied returns
`VALIDATION` with field `environmentName`. Explicit names longer than the
model's 128-character limit return the same structured validation. Existing
preview and environment rows keep their names and namespaces. Deleted
same-app environments release their names; namespace allocation still checks
retired holders because their Kubernetes resources may outlive deletion.

## Shared app workload identity

The workload identity role remains one role per app. Reconciliation builds its
policy from the union of services consumed by all live environments of that app
on the target cluster (#2093). A production deploy and a preview deploy therefore
write the same complete policy, even when the preview binds fewer services.
Duplicate bindings contribute a grant once. The namespace trust is built from
the same eligible consumers.

Private services must have a live source environment of the same app on that
cluster. Project services must belong to the app's live home project and target
the same cluster, with a live attachment to an eligible app environment.
Deleted services, environments or attachments, foreign owners, mismatched
source environments and other clusters contribute no grants. A deleted or
foreign target cluster or an app with incoherent or retired ownership fails
before resolving the IAM driver.

Concurrent deployments serialize the shared policy and trust reconciliation
on the app row. Azure assignment outcomes are recorded on each actual consuming
environment and service, including failed outcomes before the activity raises.
An empty AWS grant union removes only `astrolift-workload-policy`, the inline
policy the platform owns; external inline and attached managed policies remain.
The reconcile identity therefore needs `iam:DeleteRolePolicy` alongside the
existing role and policy write permissions. An environment with no eligible
services still skips workload identity, matching its deployment render.

This changes neither recorded namespaces nor the role naming contract. It does
not relocate existing environment workloads or combine policies across clusters.
