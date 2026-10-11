# Cloudflare DNS connections

The `domains.cloudflare_connections` capability exposes org-owned, encrypted
connections and read-only Cloudflare discovery. Capability availability does not
prove current authorization, configured OAuth, DNS write access, delegation,
certificate readiness, or live provider acceptance. This is the read-only foundation
for #1787/#2287; managed DNS writes remain unfinished.

An active platform superadmin also needs the current selected-org
`provider_plugin.read` / `provider_plugin.configure` grants and credential scope
ceiling. Staff status or organization administration alone does not satisfy this
operator gate. Team-limited or foreign-org credentials do not become org-wide
credentials. Browser sessions and bearer ownership, membership and permissions
are checked again around native responses and locked writes.

## Connect and select a zone

1. Read `dnsProviderConnectionSupport`. `allowed` is a current setup hint;
   every operation performs its own admission. An unconfigured OAuth client
   leaves the write-only API-token path available.
2. Use a Cloudflare API token restricted to the intended zones with **Zone Read**
   and **DNS Read**, or start configured OAuth from the same selected-org browser
   session. Token input is encrypted with the existing installation secret backend;
   response DTOs never return it. Configure and retain the installation encryption
   key before accepting credentials.
3. Read complete `cloudflareDnsZones`, then `cloudflareDnsRecords` for the exact
   chosen connection GUID/version and zone ID/name. Normalized record content is
   intentionally visible to the admitted operator, including bounded TXT content;
   raw provider responses, comments and arbitrary labels are not returned.
4. For an existing registration, use `attachCloudflareDnsZone` with its exact
   domain GUID/version. This adds a protected connection FK and server-owned
   native zone ID/version; it preserves the existing DNS writer, TXT challenge,
   DNS configuration and app routing. It does not replace the existing writer.
5. For a new registration, use `registerCloudflareDnsZone`. It creates an org-owned
   `cloudflare_read_only` registration with routing default `none` and a TXT
   proof challenge. Generic `createManagedDomain` refuses that driver. Read the
   challenge returned by the accepted receipt, publish it yourself, and use
   `verifyManagedDomain` for that exact zone. Verification confirms the observed
   challenge and does not start certificate or DNS-write provisioning. Its TXT
   lookup uses the existing installation resolver path; public delegation and
   public reachability diagnostics are separate observations.

`dnsProviderDomainBinding(domainId)` recovers the stored tuple after navigation
or a lost reply, reports current vs changed connection versions, and exposes
`canVerify` separately from cluster revalidation actions. Retesting advances the
connection version; explicitly rebind a stored domain with the fresh versions.
A connection that is expired, disconnected, retired or changed cannot be silently
used through an older binding. A lost register/attach reply requires a fresh
registration/binding read rather than an automatic repeated mutation.

Readonly registrations are excluded from app routing defaults and writer lookup.
Deleting one removes only its local registration; it never selects an unrelated
cluster or deletes a provider zone. Auxiliary attachment to an existing writer
preserves that writer's ordinary lifecycle.

## OAuth installation

Use a registered **Authorization Code** Cloudflare client and its operator-reviewed
redirect URI. The server sends PKCE S256 even for a confidential client and uses
`client_secret_post`. Configure:

- `ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_ID`
- `ASTROLIFT_CLOUDFLARE_OAUTH_CLIENT_SECRET` through the installation secret environment
- `ASTROLIFT_CLOUDFLARE_OAUTH_READ_SCOPES`: exactly two verified Zone/DNS read scope
  identifiers, space-separated, obtained from Cloudflare's actual OAuth scopes
  catalogue and registered client. Do not copy illustrative test scope names.
- `APP_BASE_URL`: the exact public HTTPS application origin. Register
  `<APP_BASE_URL>/api/clusters/dns/cloudflare/callback/` with Cloudflare.

The only authorization/token/revocation origins are the fixed
`https://dash.cloudflare.com/oauth2/auth`, `/oauth2/token` and `/oauth2/revoke`.
Native discovery uses fixed `https://api.cloudflare.com/client/v4` with verified
TLS, no ambient proxy, redirects, alternate origins or automatic retries.

State expires after ten minutes, is stored only as a hash, and binds the original
actor/org/browser session and configured client. A standard headerless browser
redirect can restore only that server-owned attempt's org after fresh original
session authentication; an explicit conflicting org selection still refuses. The encrypted PKCE verifier is
wiped when the attempt is consumed **before** the one token exchange. Expired,
replayed, changed-client or foreign-session attempts refuse. A lost exchange reply
is unconfirmed and not retried; start a new attempt only after reviewing the
provider account. No refresh/offline-access flow is implemented. Returned access
expiry must be explicit and bounded to at most one day; reconnect when it expires.

**Installation requirement:** exclude/redact query strings for this callback in
load balancer, reverse proxy and request-access logs before enabling OAuth. The
application scrubs its current OpenTelemetry URL attributes and returns only a
fixed saved/unconfirmed redirect with no-store/no-referrer headers. This does not
retroactively redact upstream logs, browser history or another custom exporter.
Do not collect callback code, state, PKCE verifier, client secret, bearer header,
provider error body or credential-bearing input in custom instrumentation.

## Disconnect and recovery

Local disconnection commits and wipes ciphertext first. API-token disconnection
reports `LOCAL_ONLY`: a DNS-read token cannot revoke itself through an assumed
provider permission. Revoke that token at Cloudflare separately when required.
OAuth attempts one remote revocation with the original client ID and current
matching client configuration. A confirmed successful response records
`OAUTH_REVOKED`; missing keys, a changed client, withdrawal, lost replies or failed
receipt persistence leave `OAUTH_UNCONFIRMED` while local disconnection remains
accepted. No revocation POST is blindly retried or secret restored. Neither state
claims that already dispatched provider reads were cancelled.

Inventories require complete consistent pagination: at most 400 entries across
eight 50-entry pages, twelve native requests, 512 KiB per response, 2 MiB total,
and a 30-second operation budget. Oversize, malformed, changed or incomplete
inventories report unavailable rather than a verified empty list. Unauthorized,
forbidden and rate-limited outcomes are separate safe reasons. Actor/org request
limits use the configured cache; LocMem limits are per process, not a fleet quota.

Migration `0023_dns_provider_connections` refuses rollback while any connection
or OAuth-attempt history remains, including retired rows. Preserve protected
registrations/history and ciphertext backups; this is a reviewer-visible rollback
constraint, not permission to delete history to force a production downgrade.

Remaining managed-write work includes explicit DNS-edit admission, a current
connection-backed writer in the real provisioning/certificate workflow,
record-change review/recovery, delegation observation, and live installation
acceptance. Read-only metadata or TXT verification does not complete #1787.

## Provider contracts

- [Create a Cloudflare OAuth client](https://developers.cloudflare.com/fundamentals/oauth/create-an-oauth-client/)
- [Integrate Cloudflare OAuth](https://developers.cloudflare.com/fundamentals/oauth/integrate-with-cloudflare/)
- [List OAuth scope identifiers](https://developers.cloudflare.com/api/typescript/resources/iam/subresources/oauth_scopes/methods/list/)
- [API token permissions](https://developers.cloudflare.com/fundamentals/api/reference/permissions/)
- [List zones](https://developers.cloudflare.com/api/resources/zones/methods/list/)
- [List DNS records](https://developers.cloudflare.com/api/resources/dns/subresources/records/methods/list/)
