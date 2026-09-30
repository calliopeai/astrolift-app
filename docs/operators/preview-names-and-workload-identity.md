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
