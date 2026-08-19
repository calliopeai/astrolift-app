# Runbook: Adopting an existing Azure managed resource

Astrolift refuses to update, snapshot, restore or delete an Azure managed
resource unless the resource's own ARM tags prove it belongs to the managed
service asking. That check is fail-closed and it has no exception for
resources the platform itself created before it started stamping an identity
tag: those are refused too, teardown included, and until they are adopted the
only way to remove one is by hand in the Azure portal or CLI.

**Adoption is how you fix that.** It is a separately authorized operation
that reads what the resource currently carries, records it, and then stamps
the identity envelope the resource should have had. Afterwards the resource is
ordinary: update and teardown work through Astrolift again.

---

## When you need this

| Symptom | Cause |
|---|---|
| Teardown fails with `external_resource_collision` and "carries no `astrolift-managed-service-id` marker" | Provisioned before the driver stamped an identity tag. Adopt it. |
| Same error, but "belongs to managed service `<guid>`" | The resource belongs to a *different* Astrolift managed service. Find out which before doing anything. |
| Same error, but "carries no `astrolift-managed-by=platform` marker" | Astrolift did not create this. Adopting it hands somebody's resource to the platform, which will then be willing to delete it. |

The message names which of the three you have. Read it before proceeding.

---

## Before you adopt

Adoption is not reversible in any useful sense: it writes tags, and the
platform will subsequently act on the resource as its own.

1. **Confirm the resource is the one the row means.** Compare the ARM resource
   id against the managed service's `backendRef`. They should describe the same
   resource. If they do not, you have a stale row, a name collision, or the
   wrong service, and none of those are adoption problems.

2. **Read the resource's current tags** and keep a copy:

   ```
   az resource show --ids "<resource-id>" --query tags
   ```

   Astrolift records these itself, but a copy in your own hands costs nothing.

3. **If the resource belongs to another managed service**, resolve that first.
   Two Astrolift services pointing at one resource means one of them will
   eventually delete the other's data. Adoption will let you take it, but only
   if you name the owner you are displacing; that is deliberate.

4. **Hold `managed_service.adopt`.** It ships on `org_owner` and `org_admin`
   only. It is not implied by `app.update` or by the managed-service CRUD
   grants: booking a database and taking over an existing one are different
   acts. An install that wants a narrower role to hold it should add it to a
   custom role rather than widening a system one.

---

## Adopt

```graphql
mutation {
  adoptManagedResource(input: {
    id: "<managed-service-guid>"
    resourceId: "/subscriptions/<sub>/resourceGroups/<rg>/providers/Microsoft.Cache/redis/<name>"
    reason: "pre-identity-tag cache from the 2025-11 install; teardown refusing"
  }) {
    ok
    errors { code message }
    data {
      classification
      priorMarkers
      stampedMarkers
      surface
    }
  }
}
```

`resourceId` is the full ARM resource id. Blob containers and file shares are
adopted through their own child ids
(`.../blobServices/default/containers/<name>`,
`.../fileServices/default/shares/<name>`) because their ownership envelope
lives in metadata rather than in tags; anything else with a child path is
refused rather than guessed at.

`reason` is required and lands in the audit record.

### Taking a resource from another managed service

Add `acknowledgedPriorOwner`, set to the exact managed-service id in the
refusal message:

```graphql
    acknowledgedPriorOwner: "<the-guid-from-the-error>"
```

A generic confirmation would not require you to have looked at the resource.
The id does, and the value you supply is what the audit record stores.

---

## After

`classification` in the response tells you what you actually approved:

| Value | Meaning |
|---|---|
| `unstamped` | Platform-created, no identity tag. The ordinary migration case. |
| `unmanaged` | No Astrolift marker at all. Astrolift now owns something it did not build. |
| `foreign_owner` | Taken from another managed service. |
| `already_owned` | Nothing changed; the resource already carried this identity. |

Verify the resource is normal again by running the operation that was failing.
A teardown that still refuses means the envelope went to the wrong surface,
which in practice means the resource id named a different resource than the
row's driver checks.

Adopted resources keep an `astrolift-adopted` tag. Drivers that guard teardown
on it (API Management today) require an extra explicit confirmation before
deleting one, on the grounds that a resource somebody else built is worth one
more question than one the platform created.

---

## The record

Every attempt is persisted, refusals included, with the actor, the reason, the
resource, the classification, and the resource's prior markers verbatim. Once
the envelope is merged the cloud no longer knows what was on the resource
before, so this record is the only place that answers "what did we take, and
what was on it".

`managed_service.resource_adopted` is emitted to the platform event log for the
activity feed and webhook subscribers.

---

## Not covered

Adoption is implemented for Azure. AWS and GCP managed resources carry the same
hazard and do not yet have the shared ownership verifier #1365 introduced on
Azure, so there is nothing for adoption to stamp against; the mutation refuses
with a clear message rather than reporting a success for a write it did not
make.
