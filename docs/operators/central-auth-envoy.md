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

1. **Set the config, keep the current class.** Cluster settings, Central
   auth, or `updateTenantCluster(id, oidcAuthConfig: {...})`.
   `client_secret` is write-only; the read side reports `client_secret_set`,
   and an update that omits a secret keeps the stored one.
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
4. **Flip the cluster.** Cluster settings, Ingress class, or
   `updateTenantCluster(id, ingressClass: "envoy")`.
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

## Custom domains on EKS

Validated, active exact custom hostnames with a serving certificate now render an
Envoy `HTTPRoute` and an ALB host rule. They use the app's public Deployment
Service in its canonical namespace. Preview namespaces never claim the same
hostname. The renderer refuses a mismatched app, environment, namespace or
cluster owner before it contacts a provider.

Prepare the existing edge front before moving a custom domain:

1. Choose an explicit ALB group and set
   `oidcAuthConfig.custom_domain_alb_group` to its name. Install the edge
   additively, then verify the `astrolift-system/astrolift-edge` Ingress has
   that group and a provisioned ALB hostname. Adding a group to an existing
   ungrouped ALB can replace the load balancer: schedule this as a separate
   DNS/TLS cutover and keep the previous front until its replacement works.
   This change never happens implicitly when a custom domain is added.
2. Point the custom domain at this edge front, check its CNAME chain and
   HTTPS target, and complete its ownership/DNS validation. An old stored
   CNAME target or an old nginx load balancer is not the new edge. Revalidate
   after changing the target; certificate validation alone does not move DNS.
3. Select an **issued ACM certificate** covering the exact hostname, from
   the edge's AWS account and region. Imported certificates work when their
   ACM ARN is stored as the domain's `certificateId`. An inline BYO PEM or a
   cert-manager Secret cannot terminate TLS at this ALB: import/select the
   certificate separately. The renderer refuses these unsupported shapes
   instead of creating an nginx Ingress or copying a private key.
4. Redeploy the canonical environment. The provider verifies the installed
   front's DNS target, actual ALB ARN/account/region and ingress-group tag,
   then reads the ACM certificate's status, host coverage and validity.
   Every custom rule joins that verified group; it cannot silently create
   a different load balancer. A failed verification returns no custom
   manifest set.

The provider needs read access to `sts:GetCallerIdentity`,
`elasticloadbalancing:DescribeLoadBalancers`,
`elasticloadbalancing:DescribeListeners`,
`elasticloadbalancing:DescribeTags`, and `acm:DescribeCertificate`, using
the cluster's configured credential. These checks do not import a
certificate, change DNS, or write to AWS APIs.

### Independent custom-host authentication

`edgeAuthEnabled` remains **off by default**, including a custom hostname
inside the central cookie zone. Opting in requires configured cluster OIDC
and an independently verified first-party callback. The supported verifier
is Cognito in the edge's AWS account and region:

- Register `https://<custom hostname>/oauth2/callback` on the configured
  client and enable the authorization-code flow. Keep the central callback.
  The provider reads `DescribeUserPool` and `DescribeUserPoolClient` before
  deployment and refuses missing, different or unverifiable callbacks.
  Callback registration is an explicit operator/IdP configuration step;
  the renderer never changes the shared client's callback list. Preserve
  those registrations in the IdP's own infrastructure configuration.
- Each opted-in hostname gets a route-specific `SecurityPolicy`. Its token
  cookie names differ from the central policy and every other custom host,
  and `cookieDomain` is omitted so the cookies are host-only. A central or
  sibling-domain session cannot satisfy this policy. Login and logout use
  the custom hostname's own `/oauth2/*` endpoints.
- The app's existing group/email access rules apply to this independent
  policy and are refreshed when access changes. Custom hostnames are kept
  out of the shared central authorization targets. Adding a custom domain
  does not alter the central callback, cookie names or cookie domain.
  The installer applies the shared edge manifests in a separate batch;
  waiting for a custom policy never prevents that shared batch from applying.
- A new or changed gate is staged behind an explicit Envoy direct-response
  503 filter. The backend batch is refused until the edge Gateway's policy
  ancestor reports `Accepted=True` with `observedGeneration` equal to the
  policy's current generation and the desired policy spec. A stale,
  missing, rejected or overridden status never opens the backend. Retrying
  after acceptance removes the temporary filter, including policy-only
  access refreshes. A failed policy apply leaves the staged route closed.

The staging filter uses the pinned Envoy 1.9
[direct-response API](https://gateway.envoyproxy.io/v1.9/tasks/traffic/direct-response/).
It does not rely on a missing Service or unresolved backend reference.
An unavailable Kubernetes API can prevent a requested change from reaching
the running edge; inspect the failed workflow and retry. An API response
does not promise that an existing gate was revoked during that outage.

Old custom-domain Ingresses are removed only after the current Envoy route
is accepted with resolved references, its intended auth policy is accepted,
and the ALB host Ingress has a provisioned target. If controllers are still
converging, the old Ingress stays; verify the new HTTPS/auth behavior and
redeploy to finish cleanup. Teardown prunes only this environment's routes,
policies, host fronts and custom Service reference grants.

### Explicitly unsupported fronts

AKS, GKE and `k8s_native` currently have no complete Envoy TLS-front and DNS
recipe. Choosing `envoy` during registration or cluster update, selecting
`envoy-gateway` for installation, or deploying through a persisted Envoy
configuration on these providers is refused before the relevant write or
workflow start. A controller-only chart installation is not a supported
HTTPS edge. Keep the existing nginx/ALB ingress class until a complete front
is available. Non-Cognito callback verification, inline BYO TLS termination,
and moving custom hosts to another cluster are also explicit operator
prerequisites rather than automatic migrations.

Wildcard custom domains and domains with stored path-routing or redirect
rules are refused during this cutover. Keep their existing ingress until
their complete routing shape is supported; the edge does not silently
serve an apex hostname or discard an existing rule set. Long exact
hostnames keep their full value in route hostnames and an annotation, with
a short ownership label that stays within Kubernetes' label limit.


### Editing central authentication in the dashboard

Cluster Settings → Central auth edits the existing public OIDC configuration.
Client, cookie and gateway secret badges report only presence; a missing flag is
unconfirmed. A blank client-secret field keeps the stored secret. A newly typed
value is write-only, and Cancel or a confirmed cluster/source change clears the
local draft. Issuer/client/host identifiers and backend refusal messages are
shown unchanged.

A saved reply means the configuration mutation was accepted. It does not verify
IdP callback registration or prove a current edge rollout. If the follow-up
cluster read fails, the accepted result stays saved and the dashboard warns;
Retry executes that actual read. A refused save keeps the reviewed fields and
its diagnostic instead of announcing success. The ingress-class change and
independent custom-host callback/cookie requirements above remain separate
operations and prerequisites.
