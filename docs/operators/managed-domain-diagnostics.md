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
