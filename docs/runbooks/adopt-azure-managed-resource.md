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

Preconditions: `managed_service.adopt` on the managed service (seeded on
`org_owner` and `org_admin` only); the `adoptManagedResource` GraphQL mutation,
run with `astro api graphql -q '<document>'` (there is no console control);
the `az` CLI signed in to the Azure subscription the service's driver names in
`subscription_id`, which is the cluster's provider config, for the tag read.
Placeholders: `<managed-service-guid>` is `id` from
`astroliftManagedServicesPage(appSlug: "<app-slug>")`; `<resource-id>` is the
full ARM resource id (`/subscriptions/<sub>/resourceGroups/<rg>/providers/...`);
`<the-guid-from-the-error>` is the managed-service id quoted in the refusal.

---

## When you need this

| Symptom | Cause |
|---|---|
| Teardown fails with `external_resource_collision` and "carries no `astrolift-managed-service-id` marker" | Provisioned before the driver stamped an identity tag. Adopt it. |
| Same error, but "belongs to managed service `<guid>`" | The resource belongs to a *different* Astrolift managed service. Find out which before doing anything. |
| Same error, but "carries no `astrolift-managed-by=platform` marker" | Astrolift did not create this. Adopting it hands somebody's resource to the platform, which will then be willing to delete it. |

The message names which of the three you have. Read it before proceeding.

- **Signature:** the update, snapshot, restore or teardown answers
  `external_resource_collision` with one of the three marker messages above.
- **Fix:** adopt the resource (below), with `acknowledgedPriorOwner` when it
  belongs to another managed service.
- **Rollback:** none; adoption writes tags and the platform acts on the
  resource afterwards. Do not adopt a resource Astrolift did not create
  unless the platform is meant to own it.

Refusals from `adoptManagedResource` itself:

- **Signature:** `PRECONDITION` `an adoption must state a reason`;
  `PRECONDITION` `refusing to adopt <resource>: it belongs to <owner>, and
  adopting it away requires naming that owner explicitly in the request`;
  `VALIDATION` `adoption is implemented for azure; <kind>/<variant> resolves
  to the <plugin> plugin` for an AWS or GCP service; `VALIDATION` `the
  <kind> driver on cluster <slug> has no subscription_id configured, so there
  is no subscription to adopt within`; `NOT_FOUND` `managed service not
  found`.
- **Fix:** supply `reason`, set `acknowledgedPriorOwner` to the quoted owner,
  or stop: AWS and GCP adoption is not implemented (see Not covered).
- **Rollback:** no rollback needed; a refused adoption writes nothing.

---

## Before you adopt

Adoption is not reversible in any useful sense: it writes tags, and the
platform will subsequently act on the resource as its own.

1. **Confirm the resource is the one the row means.** Compare the ARM resource
   id against the managed service's `backendRef`. They should describe the same
   resource. If they do not, you have a stale row, a name collision, or the
   wrong service, and none of those are adoption problems.

2. **Read the resource's current tags** and keep a copy:

   ```bash
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

## Verify

```bash
az resource show --ids "<resource-id>" --query tags
```

The tags now carry `astrolift-adopted` and the platform identity envelope, the
response's `classification` is the one you expected, and the operation that was
refused (update or teardown through Astrolift) now proceeds.

---

## Not covered

Adoption is implemented for Azure. AWS and GCP managed resources carry the same
hazard and do not yet have the shared ownership verifier #1365 introduced on
Azure, so there is nothing for adoption to stamp against; the mutation refuses
with a clear message rather than reporting a success for a write it did not
make.
