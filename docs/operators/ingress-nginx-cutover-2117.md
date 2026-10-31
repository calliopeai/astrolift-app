# Moving a preserved ingress-nginx install to the Envoy edge

Some installs keep an operator-managed community ingress-nginx release: an
NLB with TLS passthrough and an oauth2-proxy `auth_request` gate in front of
the apps. Kubernetes retired ingress-nginx in March 2026
([retirement notice](https://kubernetes.io/blog/2025/11/11/ingress-nginx-retirement/)),
so these hosts move to the Envoy Gateway edge with central auth
([central-auth-envoy.md](central-auth-envoy.md), #2055). This page is the
procedure (#2117): one host at a time, each with a rollback, and the old
release removed only after every host has moved.

Preconditions: the platform operator (an active superuser) holding
`cluster.manage` on the cluster for `setIngressCutover`; a kubeconfig for the
cluster and its cloud account for the HelmRelease, Service, NLB and DNS reads
and changes; write access to the DNS zone that holds the hosts. Surface:
GraphQL via `astro api graphql`. Placeholders: `<cluster-guid>` is `id` from
`astroliftClustersPage`; `<release-name>` is the ingress-nginx HelmRelease name
from the inventory; `<host>` is a hostname on the inventory; `<edge-ip>` is the
Envoy edge load balancer address.

## What keeps the old release alive

A recipe run (`installClusterPrereqs` that is not additive) deletes the
recipe HelmReleases of components it was not given, plus legacy names. Until
the cluster records the cutover complete, it never deletes a HelmRelease that

- carries the label `astrolift.ai/managed-by=operator`, or
- is named in the cluster's preserved ingress releases.

A release whose labels cannot be read is kept too. Additive runs delete
nothing, as before.

Both are set with `setIngressCutover`, which only the platform operator may
call and which is audited as `cluster.ingress_cutover`:

```graphql
mutation {
  setIngressCutover(input: { clusterId: "<cluster-guid>", preservedReleases: ["<release-name>"] }) {
    ok
    errors { code message field }
    data { preservedReleases completedAt }
  }
}
```

`complete: true` records the cutover complete, and from then on the cleanup
treats the release like any other. `complete: false` protects it again.

## 1. Inventory

Read, with the operator's own authorized access, never a tenant credential:

- the ingress-nginx HelmRelease (name, namespace, chart version, values), its
  Service and NLB, and the oauth2-proxy deployment that answers its
  `auth_request`;
- every Ingress on the `nginx` class: host, path, backend Service, TLS
  secret, and the auth annotations it carries;
- each host's DNS record, its current target and TTL.

Keep the installation's identifiers (account, load balancer, zone and host
names) in the private operational receipt for the install, not in an issue,
a PR or this page. Then protect the release before anything else changes:
label it `astrolift.ai/managed-by=operator`, or name it in
`preservedReleases`.

## 2. Prove the replacement before moving a host

On the Envoy edge, with no host moved yet:

- **TLS.** The edge serves a certificate valid for every host on the list,
  with the full chain, from the zone's wildcard or its own certificate.
- **Login.** An unauthenticated request to a gated host redirects to the
  IdP with `redirect_uri=https://<auth host>/oauth2/callback`: the single
  central callback, never a per-host one.
- **Logout.** The central logout clears the session for every host under the
  parent zone ([central-auth-logout.md](central-auth-logout.md)).
- **Identity proof.** A test backend behind the edge receives
  `X-Auth-Request-User` and `X-Auth-Request-Email` from the verified token,
  never values a client sent, and the gateway proof header
  (`X-Astrolift-Gateway-Secret` or the app's `[edge] gateway_secret_header`)
  when the cluster has a `gateway_secret` (#1726).

Stop here if any of these fails; nothing has moved yet.

## 3. Move one host

1. Lower the host's DNS TTL a day ahead, so a rollback takes effect quickly.
2. Put the host's route on the edge (redeploy the app on the `envoy` class,
   or apply its HTTPRoute) and confirm the edge serves it by resolving the
   host to the edge directly, with login, logout and the identity headers
   as in step 2:

   ```sh
   curl -sSI --resolve <host>:443:<edge-ip> https://<host>/
   ```

3. Point the host's DNS record at the edge. Leave its Ingress on ingress-nginx
   in place.
4. Watch for at least one full traffic cycle: request rate, 4xx and 5xx rate
   and latency on the edge for this host, against the same host's numbers on
   ingress-nginx before the move, and the app's live logs for auth or
   header errors.
5. Record the result in the receipt, then move the next host.

### Rolling a host back

Point the host's DNS record back at the ingress-nginx NLB. Its Ingress and
the oauth2-proxy gate are still there, so service returns as the TTL expires.
Remove the host's route from the edge afterwards if it should not answer
there. The other hosts are unaffected.

## 4. Remove the old release

Only after every host on the inventory has moved and been verified:

1. Confirm no DNS record still targets the NLB and ingress-nginx serves no
   traffic.
2. Record the cutover complete:

   ```graphql
   mutation {
     setIngressCutover(input: { clusterId: "<cluster-guid>", complete: true }) {
       ok
       errors { code message field }
       data { preservedReleases completedAt }
     }
   }
   ```

3. Delete the old Ingresses, the oauth2-proxy gate and the ingress-nginx
   release (or let the next recipe run that does not select it prune it).
   The NLB goes with its Service.

Until step 2, a recipe run never removes the release, so a host moved back in
a rollback always has somewhere to go.

## Verify

```sh
dig +short <host>
curl -sSI --resolve <host>:443:<edge-ip> https://<host>/
./run.sh test astrolift_workflows/tests/test_ingress_cutover_2117.py
```

For every moved host `dig` returns the edge address, the `curl` answers with the edge certificate and a redirect to the IdP (or the app for an ungated host), the step 4 mutation returned a non-null `completedAt`, and the suite passes.
