# Edge auth handoff for custom domains on nginx clusters (design, #1631)

Status: **design for review, 2026-11-05.** Nothing here is approved, built or
released. No product code ships with this document.

Inputs: `core/app_deploy.py` (`oidc_auth_for_cluster`,
`custom_domain_edge_auth_state`), `providers/k8s_native/central_auth.py`,
`providers/k8s_native/ingress.py`, `providers/k8s_native/edge_gateway.py`,
`astrolift_workflows/activities/app_lifecycle.py` (custom-domain loop),
`astrolift_lifecycle/models/{ingress,domain_handoff}.py`,
`core/domain_handoff.py`, `astrolift_lifecycle/domain_handoff_views.py`, and the
installer at `origin/main` (`terraform/k8s/cluster-addons/edge.tf`,
`terraform/k8s/workbench-serving/ingress.tf`).

## 1. Problem

A custom domain is a hostname the platform did not assign, in a zone outside the
platform's (`shop.example.com`, not `app.apps.example.net`). The product owner
wants it to sit behind the same edge login as the app's managed host, with one
hard constraint: **the identity provider sees exactly one callback URL, on the
central auth host.** No per-domain redirect URI, ever.

Why the current design cannot do it:

- The cluster edge is ingress-nginx plus one oauth2-proxy at the central auth
  host (`auth.apps.example.net`). Managed hosts gate with `auth-url` and
  `auth-signin` pointing at it.
- The session cookie is scoped to the auth host's parent zone
  (`cookie_scope_for` gives `.apps.example.net`). A response from
  `auth.apps.example.net` cannot set a cookie for `example.com`; browsers refuse
  it. `--cookie-domain` and `--whitelist-domain` do not change that, so pointing
  a custom domain at the central host loops forever (the #1621 finding).

## 2. What is already in the tree (read this first)

This design is not greenfield. #1631 left pieces that disagree with each other:

| Piece | State today |
|---|---|
| `CustomDomain.edge_auth_enabled` | Exists, default off. No GraphQL mutation in this checkout sets it. |
| Render for an opted-in nginx domain | Stamps `auth-url: https://<host>/oauth2/auth` and `auth-signin: https://<host>/oauth2/start?rd=...`. **Nothing in this repo serves `/oauth2/*` on that host**, so the request reaches the app. I found no per-domain proxy, no Ingress for that path. |
| `custom_domain_edge_auth_state` | Returns `gated` for any opted-in nginx domain on a cluster with OIDC, whether or not an endpoint exists. The API can therefore report `gated` for a domain whose auth check cannot succeed. |
| Identity headers and gateway secret | Not stamped on custom-domain Ingresses at all (`nginx_auth_annotations` is not called in the custom-domain loop), so even a working gate would give the app no identity and no proof of passage (#1726). |
| `DomainSessionHandoff`, `core/domain_handoff.py`, `POST /api/edge/v1/domain-handoff/exchange/` | An opaque, hash-only, 60 s, audience-bound, single-use (conditional UPDATE) grant and its exchange endpoint, with tests. `mint_domain_handoff` has **no caller**. |
| Envoy edge | Custom domains get their own `SecurityPolicy` with `redirectURL = https://<host>/oauth2/callback` (`custom_domain_security_policies`), and the cutover validates that callback at the IdP. That is the per-domain registration this design must avoid. |

So this document specifies the missing middle (who mints, who verifies, who
serves the route, what the cookie is) and tells the existing scaffolding what it
becomes. Section 12 lists the cleanup.

## 3. Goals and non-goals

Goals

1. An opted-in, validated custom domain on an nginx-class cluster requires the
   same login, same IdP, same access decision as the app's managed host.
2. One IdP callback per cluster, unchanged: `https://auth.apps.example.net/oauth2/callback`.
3. A host-only session per custom domain; no credential valid on two hosts.
4. Fail closed: a broken handoff denies, never serves ungated.
5. Per-domain opt-in; default off; no change to live traffic until chosen.

Non-goals

- Envoy edge and ALB/Cognito edge (they keep their own paths; Envoy's per-domain
  callback is listed as a rejected alternative, section 15).
- Wildcard custom domains (exact hostnames only, as on the Envoy path).
- Single logout across hosts (section 9 says what we do instead).
- Per-app access rules on nginx (I did not verify whether the nginx path
  enforces them today; this design carries the identity, not new policy).
- Replacing oauth2-proxy on managed hosts.

## 4. The pattern, evaluated

Redirect back from the central host to the custom domain's own route carrying a
short-lived, signed, single-use ticket bound to that host; the route verifies it
and sets a host-only cookie; nginx's auth check validates that cookie.

**Sound, with four conditions.** It is the standard shape of cross-domain SSO
(central login, per-site session). It works here because only the final hop sets
a cookie and that hop is a response from the custom domain itself. The
conditions that make it safe rather than merely functional:

1. The central host redirects only to registered hosts (section 8).
2. The ticket is bound to the browser that started the login (the state cookie,
   section 8), or login CSRF logs a victim in as the attacker.
3. Single use is enforced by a shared store, not per-replica memory (section 7).
4. The host cookie is short-lived and renewed through the central session, so
   central revocation reaches custom domains (section 9).

Cost to accept: for a domain's first visit the browser makes two extra
top-level redirects, and the user's access is re-checked by renewal, not
instantly. Both are bounded below.

## 5. Components and where each runs

New component: **`edge-session`**, a small stateless HTTP service, 2 replicas,
in the same namespace as oauth2-proxy (called "the auth namespace" below),
shipped as a new `BootstrapComponent` beside `oauth2-proxy` in `central_auth.py`,
`depends_on=["oauth2-proxy"]`, default off.

It does three jobs on one codebase:

| Job | Reached at | By |
|---|---|---|
| **issue**: mint a ticket for the signed-in user | `https://auth.apps.example.net/_astrolift/edge/issue` | browser, after oauth2-proxy login |
| **start / finish / logout**: set state cookie, verify ticket, set session cookie | `https://shop.example.com/_astrolift/edge/{start,finish,logout}` | browser |
| **verify**: answer nginx's auth sub-request | `http://edge-session.<auth-ns>.svc/verify`, cluster-internal only, on no Ingress | ingress-nginx |

Routing the custom-domain paths. The control plane renders, per opted-in domain,
a second Ingress in the **app** namespace for the same host and TLS secret, path
prefix `/_astrolift/edge/`, **no auth annotations**, backed by an `ExternalName`
Service in the app namespace that points at `edge-session.<auth-ns>`. More
specific prefix wins over `/`. This keeps the control plane writing only into
namespaces it already writes (no new cross-namespace rights). The issue path on
the central host is a third Ingress in the auth namespace, gated by oauth2-proxy
with `auth-response-headers` for `X-Auth-Request-User`, `-Email`, `-Groups`.
`/_astrolift/edge/` becomes a reserved prefix on gated custom domains; the
existing `/oauth2/*` stamp is dropped so apps keep that path.

### Where the check runs, and why a new service

| Option | Verdict |
|---|---|
| **A. New `edge-session` service** (recommended) | Owns ticket, cookie, verify. Can call the control plane for authorization and single-use. About 400 lines in a language the team already ships; unit-testable with no cluster; replaceable. Cost: one more workload to run, patch and image-build. |
| B. Extend oauth2-proxy | It has no ticket concept and no plugin API; the change is a fork. Running one proxy per domain gives a per-domain callback, the thing we are avoiding. Rejected. |
| C. nginx njs or lua | Verify HMAC in lua is possible, but single use needs a store shared across controller replicas (lua shared dicts are per pod), the issue side needs the IdP session anyway, and it puts security logic in annotation snippets that we already treat as fragile (fenced block, #1726). Rejected. |

## 6. The sequence

Actors: B browser, N ingress-nginx, E `edge-session`, P oauth2-proxy, C control
plane, I identity provider. `shop.example.com` is the custom domain.

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
   live central session, steps 5's IdP leg is skipped (silent renewal).
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

Steps 1 to 7 happen once per session lifetime; step 8 is every request.

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

Algorithm: HMAC-SHA256. Issuer and verifier are the same workload, so asymmetric
signing buys nothing today. Revisit (Ed25519, public key distribution) only if
verification ever moves to a separate workload. Keys are derived per purpose
(`HKDF(master, "ticket")` versus `"session"`) so a ticket can never be accepted
as a session cookie or the reverse.

**Single-use store.** `consume` is a conditional UPDATE on the existing
`DomainSessionHandoff` row keyed by `sha256(jti)`; `issue` inserts that row.
Signature is checked first (cheap rejection of garbage with no database hit),
then audience and expiry, then the consume. As today, a wrong-audience ticket is
refused before the consume so it cannot burn a legitimate user's grant.

**Failure mode of the store: fail closed.** If C is unreachable at `issue` or
`finish`, no ticket is minted or accepted and the user sees the retry page
(section 10). Sessions already established keep working because `verify` is
stateless and never calls C. A per-replica in-memory set is not acceptable
(replay against the other replica). The cost is that C is on the login path;
that is a deliberate trade, and it matches where audit already lives.

## 8. Redirect validation, login CSRF, fixation

**No open redirect.**

- The central host never accepts a URL. `issue` takes a hostname `h` and
  resolves it with C: the host must be an exact match for an active, validated
  `CustomDomain` with `edge_auth_enabled`, on an app on this cluster, whose
  certificate is `active` or `byo`. The outgoing `Location` is
  `https://` + the stored hostname + the fixed path. Unknown host: a page on the
  central host, no redirect.
- **Same organization.** The domain's app has an organization. `authorize`
  succeeds only if the signed-in user is a member of that organization, or
  otherwise satisfies the app's access rule. Registration alone is not enough;
  a user from another tenant who reaches `issue` for someone else's hostname gets
  the no-access page, not a ticket. (Open question 3 covers the multi-org
  meaning of "same".)
- `rd` never travels to the central host. It lives in the signed state cookie
  and is validated on both ends: must start with a single `/`, must not start
  with `//` or `/\`, no control characters, length capped, and the final
  redirect is built as a same-origin relative `Location`.

**Login CSRF.** An attacker who completes their own login and tricks a victim
into loading their `finish` URL would sign the victim into the attacker's
identity. The ticket carries `nb = SHA256(n)`, and `n` exists only in the state
cookie set in the victim's own browser at step 4. A ticket delivered to a
different browser has no matching state cookie and is refused. The state cookie
is `__Host-`, HttpOnly, Secure, SameSite=Lax (Lax so the top-level GET at step 7
carries it), 10 minutes, deleted on use.

**Fixation.** `finish` ignores and replaces any incoming session cookie and
issues a new `sid`. No endpoint sets a session from a query value other than a
verified ticket. The `__Host-` prefix means a sibling subdomain cannot plant a
cookie with a `Domain` attribute that shadows ours.

**Ticket in the URL.** It is visible to logs and the browser history for 60
seconds and one use. That is why the transport is a GET redirect and not a
form POST: a cross-site POST would not carry the Lax state cookie. Mitigations:
single use, 60 s, `finish` answers with `no-referrer` and `no-store`, and the
ticket value is never logged (host and reason only, as the exchange view does).

## 9. Session cookie, lifetime, revocation, logout

Cookie `__Host-astrolift_edge`: value is a signed blob `{v, kid, aud, sub, email,
sid, iat, exp, ao}` (HMAC, purpose key). Attributes: `Secure`, `HttpOnly`,
`Path=/`, **no `Domain`** (required by `__Host-`, host-only), `SameSite=Lax`,
`Max-Age` = lifetime below. No server-side session row; `verify` is a pure
function, so a control-plane outage does not log anyone out.

**Lifetime.** `exp = iat + 1 h` (cluster setting, floor 5 min, ceiling 8 h).
When it lapses, the normal step 2 to 7 path runs again and is silent while the
central session is alive. The central session therefore governs: a renewal is
only as good as the central session plus a fresh `authorize` at `issue`.

| Event | What happens | Worst-case lag on a custom host |
|---|---|---|
| Central logout (`/auth/logout`, #1741) | Central cookie cleared; custom cookie is on another site and cannot be cleared from there | cookie lifetime (default 1 h) |
| User removed from the IdP or org | `authorize` refuses at the next renewal; oauth2-proxy may still hold a central session until its own refresh | cookie lifetime plus central lag |
| Group or org membership change | Applied at the next `authorize` | cookie lifetime |
| Emergency (compromised user or key) | Per-domain epoch bump or key rotation; both invalidate every cookie for the scope | immediate on next request |

The per-domain epoch is a number the service reads from a ConfigMap the control
plane renders; it is mixed into the session key derivation, so bumping it
invalidates that domain's cookies without touching others. Lag is the honest
price of a stateless check; the 1 h default is a product decision (question 4).
Non-navigation requests (XHR, fetch) that arrive with an expired cookie get a
401 with `X-Astrolift-Reauth: 1` and no redirect, so a background call never
bounces to the IdP; the app's next navigation renews.

**Logout.** `/_astrolift/edge/logout` clears the host cookie, then redirects to
the central `/auth/logout` flow so the IdP session ends too. Other custom hosts
expire at their own lifetimes; there is no back-channel fan-out in v1.

## 10. Failure modes and pages

Pages reuse `central_auth_pages` styling (same tokens and mark, no external
requests) and are served by `edge-session`.

| Failure | Behaviour |
|---|---|
| Ticket expired, replayed, tampered, wrong host, nonce mismatch | Restart once automatically (attempt counter in the state cookie); a second failure shows "Sign-in could not be completed" with a retry link. Hard cap of 2 attempts: **no redirect loops** (the #1621 history). |
| Host not eligible at central (unknown, disabled, cert not ready) | Page on the central host: "This address is not set up for sign-in." No redirect. |
| User not authorized for the app's org | 403 page "You don't have access to `<host>`", with a sign-in-as-someone-else link. |
| Control plane unreachable | 503 "Sign-in is temporarily unavailable", retry. Existing sessions unaffected. |
| `edge-session` down | `verify` unreachable: nginx treats the sub-request failure as deny (500/503). **Fails closed.** |
| Cookies blocked | The state cookie is missing at `finish`: page explaining that cookies are required. |

## 11. Platform API, model and render

Model and API (reuse, do not rename):

- Keep `CustomDomain.edge_auth_enabled` as the per-domain opt-in (per domain,
  not per app, as its help text already argues).
- Keep `edgeAuthState` values `no_gate | gated | ungated` (they are label
  strings). Change the **meaning** on nginx: an opted-in domain is `gated` only
  when the cluster has the handoff enabled **and** `edge-session` is ready;
  otherwise `ungated`. This fixes the misreport in section 2.
- Add a read-only `edgeAuthDetail` on the domain for the UI: `flag_off`,
  `cluster_not_enabled`, `service_not_ready`, `domain_not_validated`,
  `certificate_not_ready`, `ready`. Derived, no new column.
- Add one mutation `setAppDomainEdgeAuth(domainId, enabled)`. Enabling is the
  safe direction. **Disabling opens the domain**, so it follows the
  `app.edge.make_public` precedent: step-up permission and an audit entry.
- Cluster switch in `oidc_auth_config` (JSON, as `proxy_extra_args` does):
  `edge_handoff: true`. Absent means off.

Control-plane endpoints (service-authenticated, new or adapted from the
`/api/edge/v1/` exchange view): `authorize` (host, subject, email, groups ->
allow/deny, org, app, session ceiling, epoch) and `consume` (the existing
exchange, with `jti` instead of an opaque id and without returning identity,
since the ticket already carries it).

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

Installer. I found no `oauth2-proxy` in the installer's terraform: it is
rendered by the control plane from `central_auth_component`, not installed by
the installer. So installer work is narrow: (a) make the `edge-session` image
pullable where clusters pull images, (b) confirm the control plane may create
the Ingress and Service kinds in app namespaces (it already writes Ingresses
there) and the auth namespace for the component, (c) nothing for the Envoy
edge. Whether (b) needs a `control-plane-rbac` change is **not verified**.

## 12. Relationship to existing #1631 code

- `DomainSessionHandoff`: kept as the single-use store. `token_hash` becomes
  `sha256(jti)`; `subject` and `email` columns become optional (the ticket
  carries them) or are kept for audit. Decision in review.
- `mint_domain_handoff` gains its first caller (`issue` via `authorize`).
- `exchange_domain_handoff` becomes `consume`; it stays uniform-refusal (one
  opaque 401, reason to the log only).
- The `/oauth2/*` stamp on custom domains is removed, and the tests that pin
  `auth-url` to `https://<host>/oauth2/auth` change with it.

## 13. Signing key management

- One Secret `astrolift-edge-session` in the auth namespace, mounted only into
  `edge-session`. Content: a JSON list `[{kid, key, status: active|previous}]`.
  Generated by the control plane when the cluster enables the handoff (32
  random bytes per key). It follows how `cookie_secret` already reaches
  oauth2-proxy; the recipe references the Secret by name and never inlines it
  (`helm_values` is returned over GraphQL).
- Sign with `active`; verify with `active` and `previous`.
- **Rotation (scheduled, 90 days):** add a new key as `active`, demote the old
  to `previous`, drop it after `max(session Max-Age, ticket TTL)` plus margin
  (so after 9 h at the ceiling). Cookies signed by the old key keep working
  until then; nobody is logged out.
- **Emergency rotation:** replace the list with one new key. Every session and
  ticket dies; users re-login silently through the central session.
- The control plane does not need the key (it never verifies signatures), so it
  stores nothing beyond the Secret it renders. The service credential for
  calling C is a separate per-cluster token in its own Secret.

## 14. Observability and audit

Audit events via `astrolift_operations.audit_writer`, each with org, app,
hostname, a hash of the subject, request id; never a ticket, cookie or key:

`domain_edge_auth.enabled`, `.disabled`, `ticket_issued`, `ticket_refused`
(reason enum from `HandoffRejection` plus `nonce_mismatch`, `bad_signature`,
`host_not_eligible`, `not_authorized`), `session_established`,
`session_renewed`, `logout`, `key_rotated`, `epoch_bumped`.

Metrics: counters for issued, consumed, refused by reason, renewed; latency of
`verify` (p99 budget in the test plan); gauge for key age. Alert on a refusal
rate spike by reason (replay and tamper spikes are an attack signal) and on
`verify` 5xx, which is a customer outage because it fails closed.

## 15. Alternatives considered

1. **Per-domain callback at the IdP.** Register `https://shop.example.com/oauth2/callback`
   for each domain (what the Envoy custom-domain path does today). Simplest
   cookie story, but it is the registration the product owner excluded: manual,
   IdP-specific, capped (Cognito allows 100 callbacks per client), and a domain
   becomes an IdP-side change. Rejected.
2. **Envoy OIDC for nginx clusters.** Envoy's `oauth2` filter plus
   `SecurityPolicy` does host-only cookies per domain, but it needs the Envoy
   edge (a different data plane) and still a per-domain `redirectURL`. Not an
   option for an nginx cluster without migrating the cluster, and it does not
   meet the one-callback rule.
3. **oauth2-proxy per domain** (own callback, own cookie): per-domain IdP
   registration again, plus a proxy per host. Rejected.
4. **Serve the custom domain under the central host** (reverse proxy through
   `auth.apps.example.net`): breaks the app's origin, cookies and CORS. Rejected.
5. **Cookie-only sharing via a CNAME into the platform zone**: the customer's
   hostname is in their zone by definition; no registrable-domain trick helps.

## 16. Rollout

Flags: cluster `oidc_auth_config.edge_handoff` (default off), per-domain
`edge_auth_enabled` (default off), control-plane kill switch
`EDGE_AUTH_HANDOFF_ENABLED` (default off; off means `authorize` refuses and the
render emits no handoff objects).

1. **Honesty first** (independent of this design): make
   `custom_domain_edge_auth_state` return `ungated` for an opted-in domain whose
   endpoint is not served, and add the missing response headers and gateway
   secret to the render. No behaviour change to traffic.
2. Ship `edge-session` dark: component default off, no Ingress points at it.
3. Enable on one internal cluster with one internal domain. Run the full test
   plan, including the adversarial set, against it.
4. Canary customer domains by explicit opt-in, one at a time, each with a
   documented rollback.
5. Default stays off indefinitely; there is no bulk enable.

Safe default and rollback. Off means unchanged ungated behaviour, as today. A
gated domain whose service fails is **down, not open**. Rolling a domain back to
ungated is a deliberate step-up action (section 11), never an automatic
fallback.

## 17. Test plan

Unit (service and control plane):

- Ticket: valid; expired; `iat` in the future beyond skew; tampered claims;
  tampered MAC; wrong `kid`; unknown `v`; truncated; `alg` confusion attempts
  (there is no `alg`); ticket used as a session cookie and the reverse.
- Single use: two concurrent `finish` calls, exactly one wins (conditional
  UPDATE); replay after success; wrong audience does not burn the grant.
- Redirect: `rd` of `//evil.example`, `/\evil.example`, `https://evil.example`,
  encoded and double-encoded variants, CRLF, over-length; `h` not registered,
  registered to another org, validation lost, certificate not ready, flag off.
- Cookie: attributes exactly as specified; no `Domain`; fixation (pre-set
  cookie is replaced, new `sid`); `__Host-` rules.

Adversarial, end to end on a cluster:

| Case | Expected |
|---|---|
| Ticket replay (same ticket twice, including from another replica) | second refused, uniform page, audit `ticket_refused` |
| Ticket minted for host A presented to host B | refused, grant not burned |
| Expired ticket (wait 61 s) | refused, one automatic restart |
| Tampered signature or claims | refused before any database call |
| Open redirect via `rd`, via `h`, via a forged `Location` target | never leaves a registered host |
| Login CSRF: attacker's ticket opened in a victim browser | refused, nonce mismatch |
| Cookie fixation and sibling-subdomain cookie tossing | ignored or replaced |
| Spoofed `X-Auth-Request-User`, `X-Auth-Request-Email`, gateway-secret header on the client request | overwritten by nginx; app sees only verified values |
| Direct request to `/verify` from outside the cluster | no route; unreachable |
| Control plane down, service down | login denied, existing sessions behave as designed, never ungated |
| Central logout then wait cookie lifetime | custom host demands re-login |
| Redirect-loop probe (cookies blocked) | stops at attempt 2 with the cookie page |

Non-functional: `verify` p99 under 10 ms at expected request rate (it sits on
every request); WebSocket upgrade and large cookie headers pass; two replicas
during a key rotation; Safari and Chrome with default privacy settings.

## 18. What I could not verify

I read code and the installer at `origin/main`; I ran nothing and built no
prototype. Specifically unverified:

- The headers ingress-nginx actually sends on the auth sub-request in the
  deployed controller version (I assume `X-Original-URL`; the service must not
  trust a header the client can set). Needs a test on the real controller.
- That oauth2-proxy at the pinned chart accepts the issue URL on the central
  host as a post-login `rd` and exposes `X-Auth-Request-Groups` and an auth
  time. Assumed from the existing `whitelist-domain` comment.
- That ingress-nginx serves a second Ingress for the same host from the same
  TLS secret and honours an `ExternalName` backend under the platform's
  controller settings.
- Whether any component outside this repo already serves `/oauth2/*` on custom
  domains (I found none here), and whether anything outside the checkout sets
  `edge_auth_enabled`.
- That the cluster can reach the control plane API for `authorize` and
  `consume`, and the service-credential mechanism for it.
- Whether the control plane's RBAC may create the new objects (section 11).
- Whether nginx-class gating enforces per-app access rules today.
- Browser bounce-tracking and ITP behaviour for a stateful central host reached
  by redirect without interaction; it may purge the central cookie in some
  configurations, which would surface as extra IdP prompts.

## 19. Open questions for the product owner

1. **Language and image pipeline** for `edge-session`: which runtime, and where
   is its image built and published?
2. **Reserved path.** Is `/_astrolift/edge/` acceptable as a path no gated
   custom-domain app can use?
3. **"Same organization"** on a multi-org cluster: must the user be a member of
   the app's org, may an app's access rule admit outsiders, or is it simply the
   cluster's org?
4. **Session lifetime and revocation lag**: is a 1 h default (up to 1 h before a
   removed user loses a custom-domain session) acceptable, or do we need a
   shorter lifetime, or a server-side session list with immediate revocation
   (which puts the control plane on every request)?
5. **Disabling the gate** requires step-up: who holds that permission?
6. **Customers with their own IdP rules** (email-domain narrowing, groups): does
   the custom domain inherit the cluster's `proxy_extra_args` posture, or does
   each domain get its own?
7. Should audit for refused tickets be visible to the app's org, or operators
   only?
