# Managed-Service In-Place Updates

What a `ManagedServiceDriver` promises when it says a config key can be changed
without tearing the backing resource down, and what the platform does with that
promise. Audience: driver authors.

## The guarantee

`updateManagedService` / `updateProjectManagedService` accept a new
`ManagedService.config`. What happens next:

1. **Boundary check.** The mutation resolves the service's driver and calls
   `editable_fields()`. Every key whose value differs between the incoming
   config and the stored one must be in the returned list, or the mutation
   fails with `VALIDATION` and the message "cannot be changed in-place; use
   reprovisionManagedService". Nothing is written and no workflow starts.
2. **Precondition check.** The service must be `ACTIVE` and must have a
   `backend_ref`. A second update while the first is still `UPDATING` is
   rejected with `PRECONDITION`; that is how concurrent updates serialize.
3. **Desired state is written.** `config` becomes the new desired state,
   `applied_config` keeps the last state the provider confirmed, and the row
   moves to `UPDATING`. The two fields differ from here until the update lands.
4. **`UpdateManagedServiceWorkflow`** starts under the deterministic id
   `UpdateManagedServiceWorkflow-<service guid>`, so a re-fire single-flights.
5. **The activity calls `driver.update(UpdateSpec(handle, size, config))`** with
   the row's desired config, and honours what comes back:
   - `ok=True` — the workflow polls `status()` until `available`, then finalize
     sets `applied_config = config` and returns the row to `ACTIVE`.
   - `ok=False, retryable=True` — retried on the workflow's policy, then FAILED.
   - `ok=False, retryable=False` — FAILED immediately, `status_error` carries
     the driver's message.
6. **On failure `applied_config` is not advanced.** The row stays FAILED with
   `config` (desired) and `applied_config` (last applied) visibly different.
   There is no automatic rollback: the operator either fixes the config and
   updates again, or reprovisions.

## What `ok=True` means

**"The backing resource now matches the spec."** Nothing weaker.

Finalize turns `ok=True` into `applied_config = config` plus an `ACTIVE` row,
and nothing downstream can tell that apart from a real change. A driver that
returns `ok=True` from an `update()` that performed no provider work makes the
platform record a change to a resource nobody touched, and the operator is told
their change landed. That is the defect in
[#1376](https://github.com/calliopeai/astrolift-app/issues/1376): fifteen
drivers shipped a courtesy no-op `update()` with a message explaining that the
change really goes somewhere else, and every one of them was reported as
success.

## Deriving `editable_fields()`

The list is the subset of your `config_schema()` keys that `update()` can apply
to a live resource. Work it out per key, not per driver:

- **The key reaches the provider from `update()`** — include it. This is the
  only case where `["*"]`, the protocol default, is honest, and only when
  `update()` can apply an arbitrary key.
- **The key is only read while provisioning** — leave it out. Changing it needs
  the resource rebuilt, which is `reprovisionManagedService`.
- **The cloud refuses the change on a live resource** (StorageClass after a
  claim binds, a bucket's region, a shrinking volume) — leave it out, and also
  refuse in `update()` if the request gets there anyway, so a caller that
  bypassed the boundary check still gets a clear error rather than a cloud-side
  one.
- **Nothing qualifies** — return `[]`, and have `update()` return
  `unsupported_update(spec.handle, "<why>")`:

  ```python
  def update(self, spec: UpdateSpec) -> UpdateResult:
      return unsupported_update(
          spec.handle,
          "CNPG reconciles a rolling resize from the Cluster CRD re-applied on provision",
      )

  def editable_fields(self) -> list[str]:
      return []
  ```

  `unsupported_update()` builds `ok=False, retryable=False` with the
  `update_not_supported_in_place` marker and appends "apply this change with
  reprovisionManagedService" to your reason, so the operator gets the next step
  and Temporal does not spend its retry budget on something that can never work.

`editable_fields()` and `update()` are one contract, and
`tests/test_managed_service_update_contract.py` checks them together across
every registered driver: a driver claiming editable fields may not have an
inert `update()`, and a driver claiming none may not report `ok=True`.

## Surfacing

`ManagedServiceType` exposes `appliedConfig`, `operationKind`,
`operationWorkflowId`, `operationRunId`, `operationStartedAt` and
`operationCompletedAt`, so a client can render desired-vs-applied and point at
the run. The app settings UI disables its Edit control and explains why when a
service's `editableFields` is empty.
