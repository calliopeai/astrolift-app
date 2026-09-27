# Central auth on the Envoy Gateway edge

One IdP callback for every app on a cluster, with no ingress-nginx and no
oauth2-proxy (#2055). The edge is Envoy Gateway: a Gateway in
`astrolift-edge`, one `SecurityPolicy` that every gated app route shares,
and on EKS an ALB in front that keeps TLS on the zone's ACM certificate.

A new app needs no Cognito callback, no load balancer and no DNS record of
its own. The one registered callback is
`https://<auth host>/oauth2/callback`, and the session covers every host
under the auth host's parent zone.

## What an app receives

- `X-Auth-Request-User` (the `sub` claim) and `X-Auth-Request-Email`, set by
  the edge from the verified ID token. Whatever a client sends under those
  names is dropped at the listener, before any filter runs.
- The gateway proof header (`X-Astrolift-Gateway-Secret`, or the app's
  `[edge] gateway_secret_header`) when the cluster's config has a
  `gateway_secret` (#1726).
- Token cookies, encrypted. The session's HMAC, expiry and refresh cookies
  are removed before the request leaves the edge, so nothing an app
  receives can be replayed at another app.

Not supported on this edge: `[edge] identity_headers` (per-app header
names). A deploy of an app that declares them is refused with a message,
because a per-app mapping needs a policy of its own and that would give the
app its own session.

## From the installer

An install that chooses the Envoy edge needs none of the steps below. The
control plane's environment carries `ASTROLIFT_CLUSTER_INGRESS_CLASS=envoy`
and the `ASTROLIFT_CLUSTER_OIDC_*` values (discovery URL, client id, client
secret, auth host; no cookie secret). On start, `register_tenant_cluster`
writes them to the cluster and starts an **additive** install of
`envoy-gateway`: it applies the edge and deletes no other release. A
restart once the edge is installed starts nothing (#2130).

None of this happens on a cluster whose class is not `envoy`, so an existing
install on nginx or ALB auth is left as it is until an operator moves it.

## Turning it on by hand

The cluster's `oidcAuthConfig` needs `discovery_url`, `client_id`,
`client_secret` and `auth_proxy_host`. The client must have exactly one
callback, `https://<auth_proxy_host>/oauth2/callback`. `jwks_uri`
overrides where the signing keys are read from, for a provider that does
not serve `<issuer>/.well-known/jwks.json`.

1. **Set the config, keep the current class.**
   `updateTenantCluster(id, oidcAuthConfig: {...})`. `client_secret` is
   write-only; the read side reports `client_secret_set`.
2. **Install the edge.** Flipping the class (step 4) installs it on its
   own, additively. To install it first and check it before moving
   anything, run `installClusterPrereqs` with `envoy-gateway` in
   `selectedComponents`, **plus every component the cluster already runs
   through the recipe**: an operator's install deletes the recipe
   HelmRelease of any component it is not given, so check the cluster
   status tab first and carry the whole current set forward.
3. **Check the edge before moving anything.**
   - `https://<auth host>/` redirects to the IdP with
     `redirect_uri=https://<auth host>/oauth2/callback`.
   - In `astrolift-edge`, the `SecurityPolicy` `edge-oidc` is `Accepted` and
     the `Gateway` `edge` is `Programmed`.
   - Route53 has a `*.<zone>` record pointing at the new ALB. Every app
     that still has its own ALB Ingress keeps its own, more specific
     record, so nothing has moved yet.
4. **Flip the cluster.** `updateTenantCluster(id, ingressClass: "envoy")`.
   Nothing changes for running apps until each is redeployed. The flip no
   longer commits the new gate to every app's `astrolift.toml`, because
   that commit redeployed every app at once (#2122). Pass
   `syncManifests: true` to commit it anyway.
5. **Move apps one at a time.** Redeploy an app. Its routes are applied on
   the edge, and once the Gateway is `Programmed` its old ALB Ingress is
   deleted. external-dns (`policy=sync`) then removes the app's own record
   and the wildcard answers for it. Expect a short gap while that record
   ages out. An external-dns running `upsert-only` never removes the old
   record: delete the app's A and TXT records in the zone by hand, or the
   app stays on its old ALB, which no longer has a rule for it.
6. **Clean up the IdP.** When no app on the cluster still renders an ALB
   Ingress, remove the per-app callback and sign-out URLs from the old
   app client.

## Rolling back

The class is cluster-wide, so a rollback is too. Flip the cluster back to
`alb` (it still needs its `albAuthConfig`) and redeploy the affected apps:
each renders its ALB Ingress again and external-dns writes its record,
which is more specific than the edge's wildcard. The edge's routes for
those apps stay behind, unreachable, until they are pruned.

## Known gaps

- Custom domains are not routed through the edge yet.
- The edge is wired into the EKS recipe only.
