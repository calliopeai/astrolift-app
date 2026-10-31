# Managed domains: zone, certificate, delegation

A managed domain is a DNS zone the platform writes app hostnames into
(`ManagedDomain`, `backend/astrolift_clusters/models/managed_domain.py`). One
zone per install is the default for tenant apps; organizations may register
their own. This page covers how a zone becomes active and what to do when it
stalls. Diagnostics are in [managed-domain-diagnostics.md](managed-domain-diagnostics.md);
routing a zone to a public Lambda app is in
[managed-domain-app-routing.md](managed-domain-app-routing.md).

Preconditions: `cluster.manage` on the organization (a platform operator for
shared zones); the backend task role or IRSA role in the cluster's AWS account
holding the Route 53 and ACM actions listed below; for manage.py steps, a shell
in the backend container. Placeholders: `<zone>` is the apex you register
(`astro operator domains list` shows registered ones); `<cluster guid>` is `id` from
`astro api graphql -q 'query { astroliftClustersPage { items { id slug } } }'`; `<id>` is the domain GUID
from `astro operator domains list` or `astroliftManagedDomains`.

## Two ways a zone gets registered

**From the installer, at container start.** `startup.py` runs
`manage.py bootstrap_managed_domain` on every boot. With
`ASTROLIFT_MANAGED_DOMAIN_ZONE`, `ASTROLIFT_MANAGED_DOMAIN_ZONE_ID` and
`ASTROLIFT_MANAGED_DOMAIN_CERTIFICATE_ARN` set, it writes one shared row
(`organization = NULL`, `defaultFor: tenant_apps`, wildcard managed,
`provision_state: mark_active`, verification `not_required`) in the shape
`ProvisionManagedDomainWorkflow` leaves behind. Create-only: a zone that already
has a row is left as it is, so later edits survive restarts. The zone, its hosted
zone and its wildcard certificate are the installer's; Astrolift only records
them. Unset variables make the command a silent no-op.

**From the console or API.** `createManagedDomain(input)` registers the row;
`provisionManagedDomain(clusterId, zone, isPlatformManagedZone)` starts the
workflow. The CLI mirrors the registration half:

```bash
astro operator domains list
astro operator domains create <zone> --dns-driver aws [--default-for tenant_apps|preview_envs|both|none] [--wildcard] [--shared] [--dns-config file.json]
astro operator domains update <id> [--default-for ...] [--wildcard=false] [--dns-config file.json]
astro operator domains verify <zone>
```

`--shared` needs platform-operator permission. Shared rows (no organization)
and the clusters they bind to require `cluster.manage` held by a platform
operator (`_require_operator_for_shared`). The console lives at `/domains`
(`/settings/managed-domains` redirects there); a zone's page is `/domains/[id]`.

An organization-owned zone the operator controls must pass the TXT proof
(`verifyManagedDomain`, `astro operator domains verify`) before it serves apps; the
installer's zone skips it (`verification_state: not_required`).

## ProvisionManagedDomainWorkflow

Workflow id `ProvisionManagedDomainWorkflow-<cluster guid>-<zone with dots as dashes>`.
Activities are in `backend/astrolift_workflows/activities/provision_managed_domain.py`;
the row's `provision_state` records the step reached and
`provision_failed_step` / `provision_failure_reason` record a stall (#2389).

| Step | Activity | What it does |
|---|---|---|
| `provision_dns_zone` (only with `isPlatformManagedZone: true`) | `astrolift.managed_domain.provision_dns_zone` | Creates the hosted zone through the cluster's DNS driver and stores the returned nameservers on `provision_nameservers`. On AWS a zone whose name already exists in the account is refused rather than duplicated (#1931); a zone the platform itself created earlier is adopted (`adopt_platform_zone`). |
| delegation write | `astrolift.managed_domain.delegate_parent_zone` | When the domain is bound to a Cloudflare connection holding a delegation grant for the parent zone (#1787, `bindManagedDomainDelegation`), writes the NS records at the exact child name in the parent. Only NS, only at that name, never updates or deletes. Unbound domains leave this to the operator. |
| `configure_cert_policy` | `astrolift.managed_domain.request_wildcard_cert` | Requests a DNS-validated certificate for `*.<zone>` with SANs `<zone>` and `*.<zone>`, then writes every validation CNAME into the zone. On AWS the driver re-reads the certificate with backoff for up to 60 s until ACM has published a record for every domain (#2519); past that it raises `TransientError` and Temporal retries. |
| cert poll | `astrolift.managed_domain.poll_cert_issuance` | Every 30 s, up to 144 attempts (72 h). The `revalidate` signal checks at once; `reissue` deletes the certificate and requests a new one. Missing validation records are re-written on each poll (`_heal_validation_records`). |
| `register_managed_domain` | `astrolift.managed_domain.register_row` | Upserts the row with zone id and certificate id. |
| `validate_ns_delegation` | `astrolift.managed_domain.validate_ns_delegation` | Compares the zone's public NS set with `provision_nameservers`. Checked every 5 min for up to 48 h (#2389). Zones without platform nameservers (`isPlatformManagedZone: false`) pass this step; a resolver failure is recorded and also passes. |
| `mark_active` | `astrolift.managed_domain.mark_active` | Flips the zone active. Apps are never provisioned into a zone whose registrar still points elsewhere. |

A run that ends without activating records the failing step and reason on the
row, and the console shows `Stalled at <step>: <reason>; Revalidate restarts it.`

### Operator actions

- `revalidateManagedDomain(clusterId, zone)`: signals a running workflow; when
  no run exists it starts the workflow again, so this is also the restart for a
  stalled zone.
- `reissueManagedDomainCert(clusterId, zone)`: signals `reissue`; the old
  certificate is deleted and a fresh request made. Use it when ACM stays
  `pending` after the validation records are verifiably published.
- `softDeleteManagedDomain` removes the row and starts
  `DeprovisionManagedDomainWorkflow-<cluster guid>-<zone>`, which deletes a
  platform-created hosted zone and every record in it except the apex NS/SOA
  pair.

Domains bound to a managed-domain app route (#2299) refuse provision, reissue
and delete with `PROTECTED_DOMAIN_ROUTE_MANAGE_REQUIRED`; manage the route first.

## AWS IAM the worker needs

The backend task role (the credentials the Route53 driver runs with) needs
these actions for the workflow above, from the boto3 calls in
`backend/providers/aws/dns_route53.py`:

- Route 53: `route53:CreateHostedZone` (platform-managed zones),
  `route53:GetHostedZone`, `route53:ListHostedZones`,
  `route53:ListHostedZonesByName`, `route53:ListResourceRecordSets`,
  `route53:ChangeResourceRecordSets`, `route53:ListTagsForResource`,
  `route53:DeleteHostedZone` (deprovision).
- ACM: `acm:RequestCertificate`, `acm:DescribeCertificate`,
  `acm:ListCertificates`, `acm:DeleteCertificate` (reissue and cleanup).

Without `route53:CreateHostedZone` a platform-managed provision stalls at
`provision_dns_zone` with an `AccessDenied` reason on the row after the
activity's retries (seen on an install whose task role was maintained by hand
and never picked up the installer's policy). The installer grants these in the
`task_managed_domains` policy of `calliope-installer`
`terraform/aws/astrolift-deployment-ecs/iam.tf` (`CreateHostedZone`,
`DeleteHostedZone`, `ListHostedZonesByName`, `ChangeTagsForResource`,
`ListTagsForResource` on top of the record actions); an install with its own
IAM must mirror that policy. The role is not in this repo. The IRSA role Astrolift provisions
for `external-dns` (`EXTERNAL_DNS_POLICY_STATEMENTS`,
`backend/providers/aws/identity_irsa.py`) holds only
`route53:ChangeResourceRecordSets` on hosted zones plus the three list actions;
it does not create zones.

## Platform default zone deleted: new apps come up with no hostname

The platform-level row (`organization` NULL, `default_for` `both` or
`tenant_apps`) is what every app in every organization resolves through unless
the org registered its own zone. Deleting it is accepted without a guard
(#2572). Nothing visible breaks at that moment: apps that already deployed keep
their Ingress objects and their `AppEnvironment.url`. Every app registered
afterwards resolves no zone.

**Signature:** the app reads `Ready` and its deployment reads `Running`, the
overview URL card shows the bare subdomain with no zone, the host answers the
ingress controller's generic `404 Not Found` (an invented hostname under the
same zone answers the identical page), and the namespace holds a Deployment,
Service and pod but no Ingress, Certificate or `HostnameClaim`. Confirm the
cause from the backend container:

```bash
python manage.py shell -c "
from astrolift_clusters.models import ManagedDomain
for m in ManagedDomain._base_manager.filter(organization__isnull=True):
    print(m.pk, m.zone, m.default_for, m.deleted_at)
"
```

A platform row with `deleted_at` set is the cause. `astro operator domains list`
does not show it because the list excludes deleted rows.

**Fix:**

1. Restore the row. `<pk>` is the id the query above printed:
   ```bash
   python manage.py shell -c "
   from astrolift_clusters.models import ManagedDomain
   ManagedDomain._base_manager.filter(pk=<pk>).update(deleted_at=None, deleted_by=None)
   "
   ```
2. Bind the environments created while it was gone and rewrite their URLs:
   ```bash
   python manage.py backfill_managed_domain --dry-run
   python manage.py backfill_managed_domain
   ```
   Each affected row prints as `SET <app>/<env> -> <zone> (url (empty) -> https://...)`.
3. Deploy each affected app once (Deploy button, `astro app deploy --app <slug>`,
   or `astro app reprovision --app <slug>` once #2573 ships). The render reads
   the binding; only a deploy creates the Ingress, Certificate and claim.

**Rollback:** none needed; restoring the row returns the install to the state
before the deletion. If the deletion was intended, register the replacement
zone first and move the apps, then delete.

**Verify:**

```bash
kubectl get ingress,certificate -n <app namespace>
curl -sI https://<app>.<zone>/ | head -1      # 302 to central auth or the app's own 200
```

## Backfills

- `manage.py backfill_managed_zone_ids [--apply]`: writes the verified hosted
  zone id onto rows provisioned before the id was recorded; dry run by default.
- `manage.py backfill_managed_domain [--dry-run]`: binds the `ManagedDomain`
  FK on `AppEnvironment` rows that pre-date `resolve_managed_domain` and
  recomputes the URL each advertises (#1690).
- `manage.py report_shared_zone_hostname_collisions [--format json]`:
  read-only; lists every hostname two or more organizations both render inside
  a shared zone (#2012).

## Verify

```bash
# provisionState is mark_active and the zone is active
astro operator domains list
# Public delegation answers with the nameservers the workflow handed out
dig +short NS <zone>
# The run is gone from the running list (it closes on activation)
python manage.py temporal_ops list --status running | grep ProvisionManagedDomainWorkflow
```

Through GraphQL, `astroliftManagedDomain(domainId: "<id>")` shows
`provisionState`, `provisionNameservers` and `provisionValidationRecords`;
`astroliftManagedDomainDiagnostics(domainId:, expectedVersion:)` gives the
public delegation and certificate observations.

## Related

- [managed-domain-diagnostics.md](managed-domain-diagnostics.md)
- [managed-domain-app-routing.md](managed-domain-app-routing.md)
- [cloudflare-dns-connections.md](cloudflare-dns-connections.md)
