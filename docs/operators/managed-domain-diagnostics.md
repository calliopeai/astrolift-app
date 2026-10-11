# Managed-domain diagnostics

The additive `domains.managed_diagnostics` capability advertises read-only
observations. It does not activate a domain, change delegation, issue a
certificate, select an organization default, or rewrite existing app URLs.

Use `astroliftManagedDomain(domainId)` to load the exact GUID and current
`version`. Refresh `astroliftManagedDomainDiagnostics(domainId, expectedVersion)`
with an uncached read. A changed version/source or withdrawn actor, membership,
credential, session or permission refuses the observation. Missing/foreign rows
return no target; transport failures have an explicit check state rather than
an empty successful inventory. Action flags are advisory; existing mutations
still enforce their own current permissions and proof requirements.

| Observation | Perspective and limit |
| --- | --- |
| Delegation | Fixed public resolvers 1.1.1.1 and 8.8.8.8; expected provider/recorded nameservers and observed nameservers are separate. A public mismatch requires reviewing delegation at the authoritative provider/registrar, not adding duplicate traffic records to an inactive zone. |
| Route53 | Current platform operator and bearer admin ceiling, exact zone ID/name readback, at most 100 records/three pages, including AliasTarget. Platform-written zone ID is preferred; an operator-configured legacy reference is labelled separately. Denied/unavailable inventory is ERROR, never empty OK. Private-zone public delegation is not applicable. |
| Cloudflare | Current platform operator and current selected-org credential admission, exact protected connection GUID/version plus native zone ID/name. Read-only provider metadata is checked around every response/page and zone metadata is compared before/after records. Complete native inventory is bounded to 400 records/eight pages; diagnostics displays at most 100 and marks truncation. `proxied` and `priority` are provider metadata, not observed public routing. Missing/stale/withdrawn bindings never fall back to ambient Route53. No DNS or certificate writes. |
| Internal DNS | UNSUPPORTED until a trusted cluster resolver transport exists. Public DNS does not prove private-zone visibility. |
| Effective defaults | Existing organization/shared domain resolver choice, separately for tenant apps and previews. `defaultFor` is a declaration, not proof that this row is the effective choice. Existing hostnames remain unchanged. |
| App routing | Up to 50 currently authorized same-org environment URLs whose host is the zone or a strict subdomain; exact app/cluster GUIDs and slugs. Recorded ingress class/URL is not observed load-balancer routing or TLS health. UNKNOWN remains explicit. |

`astroliftManagedDomainProbe(domainId, expectedVersion, hostname, tool,
recordType)` accepts only the registered zone or strict subdomains. LOOKUP and
DIG use the same bounded DNS engine: typed answers and TTL-bearing provider
inventory are available; this API does not execute a shell `dig` command or
claim raw command flags/output. Record types are A, AAAA, CNAME, NS, SOA, TXT,
MX, SRV and CAA; underscore labels are allowed for DNS record lookups only.
Packets must match the request, peer, question and answer/CNAME ownership chain.
NXDOMAIN and no-data reasons remain distinct; truncated packets are refused.

HTTPS first resolves through the fixed public resolver and rejects every mixed
public/private answer. It pins a public IP, verifies the registered hostname
with TLS SNI, sends a credential-free HEAD, and observes only the HTTP status
line under one eight-second deadline. No redirects, headers or bodies are
followed or exposed. A verified TLS/status observation is not application
health or domain activation.

The backend image installs `iputils-ping` and `traceroute`. ICMP probes use fixed
numeric-IP argv without a shell, a five-second deadline, and an 8KiB streaming
output bound; children are killed/joined on timeout, overflow or withdrawn
admission. Raw command output and internal hop addresses are not returned.
Missing binaries are UNSUPPORTED, blocked/unconfirmed ICMP is UNKNOWN, and an
ICMP response is distinct from HTTPS health. Native image package execution
must still be checked by the image build/release; local tests do not prove an
external network permits ICMP.

Before provider/socket/process work, atomic cache counters limit requests per
minute to ten per actor/org/domain, thirty per actor/org and sixty per org/domain.
GraphQL aliases consume the same allowance. Unsupported cache backends fail
closed. Use Redis for shared production limits; LocMem counters are process-local
and intended for native tests/development. A refresh always observes current
state; cached health is not returned as fresh success.

Pending ownership-proof rows can be diagnosed by their authorized owner without
performing DNS writes. Existing TXT challenge verification/provisioning remains
a separate flow. Cloudflare discovery and read-only registration use their
protected connection contract; no Route53 alias or nonexistent Cloudflare writer
should be used to imply provisioning.


For an attached Cloudflare domain, `providerZone.bindingSource` is
`PROTECTED_CLOUDFLARE_CONNECTION`. Connection expiry, disconnection, ownership or
version changes make the inventory unavailable. A domain/binding change during
any provider read refuses the whole diagnostic response; previous records are
not returned as current. Provider permission failures are explicit errors.
Tenant public DNS observations remain available under DNS-read permission;
provider credentials/records require current platform-operator admission. A new
read-only registration without recorded expected nameservers can show UNKNOWN
public delegation for a tenant reader until an authorized provider observation
is available. The TXT ownership challenge and provider read access remain
separate from writer support, effective domain selection and public activation.

Certificate registration preserves the existing domain configuration and records
its actual provisioning cluster. This keeps exact cluster links and revalidation
available after the certificate step, including when a delegation check pauses
the workflow. A legacy row missing this association needs an authorized resume
with the reviewed cluster through `revalidateManagedDomain(clusterId, zone)`;
this change does not backfill live rows or select a new app default. Revalidation
and certificate setup are separate from app ingress records and redeployment.


## Explicit defaults for future app addresses

`updateManagedDomain` now accepts optional `expectedVersion` and
`expectedDefaultsFingerprint`. New callers must review the exact domain and
`diagnostics.actions.defaultsFingerprint`, then submit both unchanged. The
fingerprint binds all live domain GUIDs, versions and role flags in that own or
shared scope, the current organization override, and own-domain precedence for
shared rows. A changed sibling or an intervening change back to the same flags
refuses the review. These values describe metadata; they do not grant authority.
Legacy callers may omit them for compatibility.

`defaultFor` accepts exactly `tenant_apps`, `preview_envs`, `both` or `none`.
Selecting a use case removes only that overlapping flag from other live domains
in the same scope. For example, selecting tenant apps on a new zone reduces an
old `both` zone to `preview_envs`; it preserves its other metadata and other
organizations. Existing environment URLs are never rewritten. Default metadata
selection requires current `provider_plugin.configure`, not cluster mutation
permission. Shared domain rows additionally require the current platform
operator. The organization row serializes own choices; shared choices also take
transaction advisory protocol key `22990001` before ordered domain row locks.
Authority, credential scope, membership, browser session and policy are reread
after these waits. Bearer admission also compares the original authenticated key,
owner, team, scopes and expiry with the freshly read credential; a replacement
key on the same row is refused. General token row versions are not credential
identity because ordinary request tracking advances them.

Non-`none` selection requires completed `mark_active` provisioning, a
platform-recorded zone identity, and a live associated cluster with an enabled,
installed matching DNS writer. Pending proof, incomplete provisioning,
read-only discovery, missing/foreign writer association and withdrawn writers
are explicit refusals; this metadata check performs no provider/network calls
and does not prove delegation, ingress or HTTPS. An explicit organization
`defaultManagedDomain` override refuses role selection because it outranks
flags for both use cases. A shared fallback also refuses when the caller's own
eligible use-case default would still win. `none` can remove unsafe role flags
but does not clear an explicit override.

`diagnostics.actions.canUpdate` describes current metadata-update eligibility;
`canSetDefaults` conservatively describes eligibility for both use cases and
`defaultUpdateReason` explains refusal. All action hints are advisory: the
mutation rechecks current authority, target version and reviewed impact under
locks. No default selection provisions resources, assigns a custom host to an
existing app or migrates a fleet. This implements the default-settings portion
of #2299; reviewed app routing remains separate.

Read the current review data using private variables for the actual domain GUID
and version:

```graphql
query ReviewManagedDomainDefault($id: GUID!, $version: Int!) {
  astroliftManagedDomainDiagnostics(domainId: $id, expectedVersion: $version) {
    id
    version
    actions {
      canUpdate
      canSetDefaults
      defaultUpdateReason
      defaultsFingerprint
    }
  }
}
```

Then submit the exact reviewed `id`, `expectedVersion`,
`expectedDefaultsFingerprint` and chosen `defaultFor` in `$input`:

```graphql
mutation SelectManagedDomainDefault($input: UpdateManagedDomainInput!) {
  updateManagedDomain(input: $input) {
    ok
    errors { code message }
    data { id version defaultFor }
  }
}
```

Refresh current diagnostics after an accepted write. Acceptance is separate
from refresh success or observed routing. A lost reply requires a fresh read
and explicit review; these legacy metadata mutations have no nonce receipt or
automatic replay contract.
Route53 inventory also exposes `providerZone.records.routing`. The typed policy
is SIMPLE, WEIGHTED, LATENCY, FAILOVER, GEOLOCATION, MULTIVALUE, GEOPROXIMITY,
CIDR or UNKNOWN. Native set identifiers, zero weights, regions, failover sides,
geographic locations, geoproximity coordinates/bias and CIDR collection/location
are preserved. Health-check and traffic-policy IDs are references, not current
health observations; weights describe configured DNS selection rather than
measured application traffic. The existing alias target and target-health setting
remain separate from routing policy. Cloudflare discovery returns no Route53
routing object.

Missing policy details or incompatible combinations remain UNKNOWN with a
reason. Malformed or oversized native values refuse the provider observation
without exposing raw provider replies. The projection uses fields recognized by
the installed AWS SDK; it does not claim visibility into fields that SDK drops.
It adds no record-write or health-check call. Public DNS answers, app ingress and
TLS observations still need their separate checks.

The field shapes follow the [Route53 record-set API](https://docs.aws.amazon.com/Route53/latest/APIReference/API_ResourceRecordSet.html),
including its [geoproximity configuration](https://docs.aws.amazon.com/Route53/latest/APIReference/API_GeoProximityLocation.html)
and [CIDR locations](https://docs.aws.amazon.com/Route53/latest/APIReference/API_CidrRoutingConfig.html).
