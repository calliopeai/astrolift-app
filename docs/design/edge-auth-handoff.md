# Edge auth handoff for custom domains on nginx clusters (design, #1631)

Status: **design for review, 2026-11-05.** Nothing here is approved or built.

Inputs: the code named below and the installer at `origin/main`.

## 1. Problem

A custom domain is a hostname the platform did not assign, in a zone outside the
platform's (`shop.example.com`, not `app.apps.example.net`). It must be able to
sit behind the same edge login as the managed host, and **the identity provider
must see exactly one callback URL, on the central auth host**: no per-domain
redirect URI.

Why it cannot be done today: the edge is ingress-nginx plus one oauth2-proxy at
the central host (`auth.apps.example.net`), and its session cookie is scoped to
that host's parent zone (`cookie_scope_for`: `.apps.example.net`). A response
from there cannot set a cookie for `example.com`, a browser rule that
`--cookie-domain` and `--whitelist-domain` do not change, so pointing a custom
domain at the central host loops forever (the #1621 finding).

## 2. What is already in the tree (read this first)

#1631 left pieces that disagree with each other (section 11 says what becomes of them):

| Piece | State today |
|---|---|
| `CustomDomain.edge_auth_enabled` | Exists, default off. No GraphQL mutation in this checkout sets it. |
| Render for an opted-in nginx domain | Stamps `auth-url: https://<host>/oauth2/auth` and `auth-signin: https://<host>/oauth2/start?rd=...`. **Nothing in this repo serves `/oauth2/*` on that host**: the sub-request is answered by the app itself, so an app that returns 2xx for unknown paths (a catch-all SPA) passes the check and the gate is a no-op (by reading, not tested). |
| `custom_domain_edge_auth_state` | Returns `gated` for any opted-in nginx domain on a cluster with OIDC, whether or not an endpoint exists. The API can therefore report `gated` for a domain whose auth check cannot succeed. |
| Identity headers and gateway secret | Not stamped on custom-domain Ingresses at all (`nginx_auth_annotations` is not called in the custom-domain loop), so even a working gate would give the app no identity and no proof of passage (#1726). |
| `DomainSessionHandoff`, `core/domain_handoff.py`, `POST /api/edge/v1/domain-handoff/exchange/` | An opaque, hash-only, 60 s, audience-bound, single-use (conditional UPDATE) grant and its exchange endpoint, with tests. `mint_domain_handoff` has **no caller**. |

## 3. Goals and non-goals

Goals: an opted-in, validated custom domain on an nginx-class cluster requires
the same login, IdP and access decision as the managed host; one IdP callback
per cluster, unchanged; a host-only session per domain; fail closed (a broken
handoff denies, never serves ungated); per-domain opt-in, default off.

Non-goals: the Envoy and ALB edges (section 14); wildcard domains; single logout
across hosts (section 9); new per-app access policy (I did not verify whether
the nginx path enforces access rules; this design carries identity, not policy);
replacing oauth2-proxy on managed hosts.

## 4. The pattern, evaluated

The pattern: redirect back from the central host to the custom domain's own
route with a short-lived, signed, single-use ticket bound to that host; the
route verifies it and sets a host-only cookie; nginx's auth check validates it.

**Sound, with four conditions.** It is the standard cross-domain SSO shape and
works here because the cookie is set by a response from the custom domain. It
is safe only if: (1) the central host redirects solely to registered hosts; (2)
the ticket is bound to the browser that started the login, or login CSRF signs a
victim in as the attacker; (3) single use lives in a store shared by all
replicas; (4) the host cookie is short and renewed through the central session,
so central revocation reaches custom domains. Cost: two extra top-level
redirects per session, and access is re-checked at renewal, not instantly.

## 5. Components and where each runs

New component: **`edge-session`**, a small stateless HTTP service, 2 replicas, in
oauth2-proxy's namespace ("the auth namespace"), a new `BootstrapComponent` beside
`oauth2-proxy` in `central_auth.py`, `depends_on=["oauth2-proxy"]`, default off.

It does three jobs on one codebase:

| Job | Reached at | By |
|---|---|---|
| **issue**: mint a ticket for the signed-in user | `https://auth.apps.example.net/_astrolift/edge/issue` | browser, after oauth2-proxy login |
| **start / finish / logout**: set state cookie, verify ticket, set session cookie | `https://shop.example.com/_astrolift/edge/{start,finish,logout}` | browser |
| **verify**: answer nginx's auth sub-request | `http://edge-session.<auth-ns>.svc/verify`, cluster-internal only, on no Ingress | ingress-nginx |

Routing. Per opted-in domain the control plane renders a second Ingress in the
**app** namespace for the same host and TLS secret, prefix `/_astrolift/edge/`,
**no auth annotations**, backed by an `ExternalName` Service in the app
namespace pointing at `edge-session.<auth-ns>` (the more specific prefix wins
over `/`; no new cross-namespace write rights). The issue path on the central
host is an Ingress in the auth namespace gated by oauth2-proxy, with
`auth-response-headers` for `X-Auth-Request-User`, `-Email`, `-Groups`.
`/_astrolift/edge/` becomes reserved on gated custom domains; the old
`/oauth2/*` stamp is dropped so apps keep that path.

### Why a new service

- **A. New `edge-session` service (recommended).** Owns ticket, cookie and
  verify; can call the control plane for authorization and single use. Small
  (a few hundred lines), unit-testable without a cluster. Cost: one more
  workload to run, patch and image-build.
- B. Extend oauth2-proxy. No ticket concept and no plugin API, so a fork; one
  proxy per domain means a per-domain callback. Rejected.
- C. nginx njs or lua. HMAC is possible, but single use needs a store shared
  across controller replicas (lua shared dicts are per pod), and it puts
  security logic in annotation snippets we already treat as fragile (#1726).
  Rejected.

## 6. The sequence

Actors: B browser, N ingress-nginx, E `edge-session`, P oauth2-proxy, C control
plane, I identity provider. Steps 1 to 7 run once per session; step 8 every request.

1. B requests `https://shop.example.com/orders`. No session cookie.
2. N sends the auth sub-request to E `/verify` with the original URL. E finds no
   valid `__Host-astrolift_edge` cookie for this host and answers 401.
3. N redirects (auth-signin) to `https://shop.example.com/_astrolift/edge/start?rd=%2Forders`.
4. E `start` (on the custom host): validates `rd` is a same-host path (section 8),
   generates random nonce `n`, **sets** `__Host-astrolift_edge_state` =
   signed `{host, n, rd, attempt, exp=10min}`, then 302 to
   `https://auth.apps.example.net/_astrolift/edge/issue?h=shop.example.com&nb=SHA256(n)`.
5. B requests the central issue URL. N runs P's auth check. No central session:
   N redirects to P `/oauth2/start?rd=<this issue URL>`; P sends B to I; B logs
   in; I redirects to the **one callback** `/oauth2/callback`; **P sets** its
   central cookie (`.apps.example.net`) and redirects to the issue URL. With a
   live central session, the IdP leg of step 5 is skipped (silent renewal).
6. E `issue` **reads** the identity headers N copied from P's auth response
   (N overwrites client-supplied copies). It checks the host (section 8) and the
   user's right to the app by calling C `authorize` (section 11). On success it
   mints the ticket and 302s to
   `https://shop.example.com/_astrolift/edge/finish?t=<ticket>`. The redirect
   target is built from the registry row, never from the request.
7. B requests `finish` on the custom host (the state cookie rides this top-level
   GET, SameSite=Lax). E **reads** the state cookie and the ticket, verifies
   signature, `aud == Host`, expiry, `nb == SHA256(n)`, then calls C `consume`
   (single use). On success E **sets** `__Host-astrolift_edge` (the session) and
   **clears** the state cookie, and 302s to `rd` with `Cache-Control: no-store`
   and `Referrer-Policy: no-referrer`.
8. B requests `/orders` with the session cookie. N's sub-request to E `/verify`
   validates it and answers 200 with `X-Auth-Request-User` and
   `X-Auth-Request-Email`; N forwards those plus the gateway secret to the app.

## 7. Ticket

Compact, not a general JWT library, so there is no `alg` header to confuse:
`v1.<base64url(claims)>.<base64url(HMAC-SHA256(key[kid], "ticket|" + claims))>`.

| Claim | Meaning |
|---|---|
| `v`, `kid` | format version, signing key id |
| `iss` | central auth host |
| `aud` | the custom hostname, lowercased, no trailing dot |
| `sub`, `email` | verified IdP subject and email from P's headers |
| `jti` | 128-bit random id, the single-use handle |
| `nb` | SHA-256 of the state nonce, binds to the starting browser |
| `iat`, `exp` | issue time, `iat + 60 s` (as `HANDOFF_TTL`) |
| `ao` | auth time of the central session, if P exposes it (else `iat`) |

Algorithm: HMAC-SHA256. Issuer and verifier are one workload, so asymmetric
signing buys nothing today (revisit if verification moves out). Keys are derived
per purpose (`HKDF(master, "ticket")` versus `"session"`) so a ticket is never
accepted as a session cookie or the reverse.

**Single-use store.** `consume` is a conditional UPDATE on the existing
`DomainSessionHandoff` row keyed by `sha256(jti)`; `issue` inserts it. The
signature is checked first (garbage never reaches the database), then audience
and expiry, then the consume; a wrong-audience ticket is refused before the
consume so it cannot burn a legitimate user's grant.

**Store failure: fail closed.** If C is unreachable at `issue` or `finish`, no
ticket is minted or accepted and the user sees the retry page (section 10).
Established sessions keep working because `verify` is stateless. A per-replica
in-memory set is not acceptable (replay against the other replica). The cost,
C on the login path, is deliberate and matches where audit already lives.

## 8. Redirect validation, login CSRF, fixation

**No open redirect.**

- The central host never accepts a URL. `issue` takes a hostname `h` and
  resolves it with C: an exact match for an active, validated `CustomDomain`
  with `edge_auth_enabled`, on an app on this cluster, certificate `active` or
  `byo`. The `Location` is `https://` + the stored hostname + a fixed path.
  Unknown host: a page on the central host, no redirect.
- **Same organization.** `authorize` succeeds only if the signed-in user is a
  member of the app's organization or otherwise satisfies the app's access
  rule. Registration alone is not enough: a user from another tenant reaching
  `issue` for someone else's hostname gets the no-access page, not a ticket
  (question 3 covers multi-org clusters).
- `rd` never travels to the central host. It lives in the signed state cookie
  and is validated at `start` and again at `finish`: a single leading `/`, not
  `//` or `/\`, no control characters, length capped, emitted as a relative
  `Location`.

**Login CSRF.** An attacker who completes their own login and tricks a victim
into loading their `finish` URL would sign the victim in as the attacker. The
ticket carries `nb = SHA256(n)` and `n` exists only in the state cookie set in
the victim's own browser at step 4, so a ticket from another browser is refused.
The state cookie is `__Host-`, HttpOnly, Secure, SameSite=Lax (so the top-level
GET at step 7 carries it), 10 minutes, deleted on use.

**Fixation.** `finish` ignores and replaces any incoming session cookie and
issues a new `sid`; nothing sets a session except a verified ticket. `__Host-`
stops a sibling subdomain planting a `Domain` cookie that shadows ours.

**Ticket in the URL.** A GET redirect, because a cross-site POST would not carry
the Lax state cookie. Mitigations: single use, 60 s, `no-referrer` and `no-store`
on `finish`, and the ticket is never logged.

## 9. Session cookie, lifetime, revocation, logout

Cookie `__Host-astrolift_edge`: value is a signed blob `{v, kid, aud, sub, email,
sid, iat, exp, ao}` (HMAC, purpose key). Attributes: `Secure`, `HttpOnly`,
`Path=/`, **no `Domain`** (required by `__Host-`, host-only), `SameSite=Lax`,
`Max-Age` = lifetime below. No server-side session row; `verify` is a pure
function, so a control-plane outage does not log anyone out.

**Lifetime.** `exp = iat + 1 h` (cluster setting, floor 5 min, ceiling 8 h).
When it lapses, steps 2 to 7 run again, silently while the central session is
alive, so the central session governs: a renewal is only as good as that session
plus a fresh `authorize` at `issue`.

Revocation lag on a custom host: central logout (`/auth/logout`, #1741) cannot
clear a cookie on another site, so the host session lasts up to its lifetime;
user removal or group or org change takes effect at the next `authorize`, so up
to the lifetime plus any lag in the central session; emergency (compromised user
or key) is immediate via a per-domain epoch bump or key rotation.

The per-domain epoch is a number the service reads from a ConfigMap the control
plane renders and mixes into the session key derivation, so bumping it kills one
domain's cookies. The lag is the honest price of a stateless check; the 1 h
default is a product decision (question 4). Non-navigation requests (XHR, fetch)
with an expired cookie get a 401 with `X-Astrolift-Reauth: 1` and no redirect,
so a background call never bounces to the IdP; the next navigation renews.

**Logout.** `/_astrolift/edge/logout` clears the host cookie and redirects to the
central `/auth/logout` flow. Other custom hosts expire at their own lifetimes;
no back-channel fan-out in v1.

## 10. Failure modes and pages

Pages reuse `central_auth_pages` styling.

| Failure | Behaviour |
|---|---|
| Ticket expired, replayed, tampered, wrong host, nonce mismatch | Restart once automatically (attempt counter in the state cookie); a second failure shows "Sign-in could not be completed" with a retry link. Hard cap of 2 attempts: **no redirect loops** (the #1621 history). |
| Host not eligible at central (unknown, disabled, cert not ready) | Page on the central host: "This address is not set up for sign-in." No redirect. |
| User not authorized for the app's org | 403 page "You don't have access to `<host>`", with a sign-in-as-someone-else link. |
| Control plane unreachable | 503 "Sign-in is temporarily unavailable", retry. Existing sessions unaffected. |
| `edge-session` down | `verify` unreachable: nginx treats the sub-request failure as deny (500/503). **Fails closed.** |
| Cookies blocked | The state cookie is missing at `finish`: page explaining that cookies are required. |

## 11. Platform API, model and render

Model and API (reuse, do not rename): keep `CustomDomain.edge_auth_enabled` as the
per-domain opt-in and the `no_gate | gated | ungated` values (they are label
strings). Change the **meaning** on nginx: an opted-in domain is `gated` only
when the cluster has the handoff enabled **and** `edge-session` is ready, else
`ungated`, fixing the misreport in section 2. Add a derived, read-only
`edgeAuthDetail` for the UI, where the opt-in is a toggle on the access side of
the domain panel with this status beside it (`flag_off`, `cluster_not_enabled`,
`service_not_ready`, `domain_not_validated`, `certificate_not_ready`, `ready`).
Add a mutation `setAppDomainEdgeAuth(domainId, enabled)` behind that toggle:
enabling is the safe direction; **disabling opens the domain**, so it takes step-up permission and an
audit entry, as `app.edge.make_public` does. A cluster switch
`oidc_auth_config.edge_handoff` (absent means off), as `proxy_extra_args` does.

Control-plane endpoints (service-authenticated, adapted from the `/api/edge/v1/`
exchange view): `authorize` (host, subject, email, groups -> allow or deny, org,
app, session ceiling, epoch) and `consume` (`jti` in, no identity out, since the
ticket carries it).

Render changes (`app_lifecycle.py` custom-domain loop, via the shared
`nginx_auth_annotations` so the three renderers cannot drift):

- `auth-url`: `http://edge-session.<auth-ns>.svc/verify`.
- `auth-signin`: `https://<host>/_astrolift/edge/start?rd=$escaped_request_uri`.
- `auth-response-headers` and the fenced gateway-secret snippet, as on managed
  hosts (closes the gap in section 2).
- Second Ingress and `ExternalName` Service for `/_astrolift/edge/`, ungated.
- `astrolift.dev/edge-auth` label from the corrected state function.
- Render nothing gated unless the domain is validated and the certificate is
  `active` or `byo` (the loop already skips otherwise); a domain that loses
  validation falls out of render, so it falls back to **no route**, not to an
  ungated route.

Existing #1631 code: `DomainSessionHandoff` stays as the single-use store with
`token_hash = sha256(jti)` (subject and email columns become optional);
`mint_domain_handoff` gains its first caller; `exchange_domain_handoff` becomes
`consume` with its uniform opaque refusal; the `/oauth2/*` stamp and the tests
pinning it go.

Installer. I found no `oauth2-proxy` in the installer's terraform: the control
plane renders it from `central_auth_component`. So installer work is narrow: make
the `edge-session` image pullable, and confirm the control plane may create the
new Ingress and Service objects (whether `control-plane-rbac` needs a change is
**not verified**). Nothing for the Envoy edge.

## 12. Signing key management

- One Secret `astrolift-edge-session` in the auth namespace, mounted only into
  `edge-session`: `[{kid, key, status: active|previous}]` (sign with `active`,
  verify with both), 32 random
  bytes per key, generated by the control plane when the cluster enables the
  handoff. Like `cookie_secret` for oauth2-proxy, the recipe references it by
  name and never inlines it (`helm_values` is returned over GraphQL).
- **Scheduled rotation (90 days):** new key `active`, old to `previous`, dropped
  after `max(session Max-Age, ticket TTL)` plus margin. Nobody is logged out.
- **Emergency rotation:** replace the list with one new key; every session and
  ticket dies and users re-login silently through the central session.
- The control plane never verifies signatures, so it needs no key; the service's
  credential for `authorize` and `consume` is a separate per-cluster token.

## 13. Observability and audit

Audit via `astrolift_operations.audit_writer`, with org, app, hostname, a hash
of the subject and the request id, never a ticket, cookie or key:
`domain_edge_auth.enabled|disabled`, `ticket_issued`, `ticket_refused` (reason
from `HandoffRejection` plus `nonce_mismatch`, `bad_signature`,
`host_not_eligible`, `not_authorized`), `session_established|renewed`, `logout`,
`key_rotated`, `epoch_bumped`. Metrics: issued, consumed, refused by reason,
renewed, `verify` latency, key age. Alert on refusal spikes by reason (replay
and tamper spikes signal attack) and on `verify` 5xx (a customer outage, since
it fails closed).

## 14. Alternatives considered

1. **Per-domain callback at the IdP** (what the Envoy custom-domain path does,
   `custom_domain_security_policies`). Simplest cookie story, but it is the
   registration the product owner excluded: manual, IdP-specific, capped
   (Cognito: 100 callbacks per client). Rejected.
2. **Envoy OIDC on an nginx cluster.** Needs the Envoy data plane and still a
   per-domain `redirectURL`. Rejected.
3. **oauth2-proxy per domain** (per-domain registration again) or **serving the
   domain through the central host** (breaks origin, cookies, CORS). Rejected.

## 15. Rollout

Flags, all default off: cluster `oidc_auth_config.edge_handoff`, per-domain
`edge_auth_enabled`, and a control-plane kill switch
`EDGE_AUTH_HANDOFF_ENABLED` (off: `authorize` refuses, render emits no handoff
objects).

1. **Honesty first** (independent): `custom_domain_edge_auth_state` returns
   `ungated` for an opted-in domain whose endpoint is not served; add the missing
   response headers and gateway secret. No traffic change.
2. Ship `edge-session` dark; then one internal cluster and domain, full test plan.
3. Customer domains by explicit opt-in, one at a time; no bulk enable.

Off means today's ungated behaviour. A gated domain whose service fails is
**down, not open**; rolling back to ungated is a deliberate step-up action
(section 11), never automatic.

## 16. Test plan

Unit: tickets (valid, expired, future `iat`, tampered, wrong `kid`, unknown `v`,
truncated, ticket as cookie and the reverse); two concurrent `finish` calls with
exactly one winner; wrong audience does not burn the grant; `rd` of
`//evil.example`, `/\evil.example`, absolute and (double-)encoded URLs, CRLF,
over-length; `h` unregistered, other org, validation lost, certificate not
ready, flag off; cookie attributes exact, no `Domain`, pre-set cookie replaced.

Adversarial, end to end on a cluster:

| Case | Expected |
|---|---|
| Ticket replay, including against the other replica | second use refused, audit `ticket_refused` |
| Ticket for host A presented at host B | refused, grant not burned |
| Expired ticket (wait 61 s) | refused, one automatic restart |
| Tampered signature or claims | refused before any database call |
| Open redirect via `rd`, `h` or a forged target | never leaves a registered host |
| Login CSRF: attacker's ticket in a victim browser | refused, nonce mismatch |
| Cookie fixation, sibling-subdomain cookie tossing | ignored or replaced |
| Spoofed `X-Auth-Request-*` and gateway-secret headers | overwritten by nginx; app sees verified values only |
| `/verify` requested from outside the cluster | no route |
| Control plane or service down | login denied, never ungated |
| Cookies blocked | stops at attempt 2 with the cookie page |

Non-functional: `verify` p99 under 10 ms; WebSocket upgrade and large cookies
pass; two replicas during rotation; Safari and Chrome defaults.

## 17. What I could not verify

I read code and the installer at `origin/main`; I ran nothing and built no
prototype. Unverified:

- The headers the deployed ingress-nginx sends on the auth sub-request (I assume
  `X-Original-URL`; the service must not trust a client-settable header).
- That the pinned oauth2-proxy accepts the central issue URL as a post-login
  `rd` and exposes `X-Auth-Request-Groups` and an auth time.
- That ingress-nginx serves a second Ingress for one host and TLS secret, and an
  `ExternalName` backend, under the platform's controller settings.
- Whether anything outside this repo serves `/oauth2/*` on custom domains or
  sets `edge_auth_enabled`; whether the cluster can reach the control plane API
  and how it authenticates; whether control-plane RBAC may create the new
  objects; whether nginx gating enforces per-app access rules today.
- Browser bounce-tracking and ITP behaviour for a central host reached by
  redirect without interaction; it may purge the central cookie, showing up as
  extra IdP prompts.

## 18. Open questions for the product owner

1. Runtime and image pipeline for `edge-session`: which language, where built
   and published?
2. Is `/_astrolift/edge/` acceptable as a path no gated custom-domain app can use?
3. "Same organization" on a multi-org cluster: must the user be a member of the
   app's org, may an access rule admit outsiders, or is it the cluster's org?
4. Is a 1 h default revocation lag acceptable, or do we need a shorter lifetime
   or a server-side session list (the control plane on every request)?
5. Who holds the step-up permission to disable a domain's gate?
6. Does a domain inherit the cluster's `proxy_extra_args` posture, or get its own?
