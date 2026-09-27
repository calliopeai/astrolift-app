# Central auth on EKS

The central auth host (#1539) gates every managed app on a cluster through
one oauth2-proxy at `auth.<zone>`. The identity provider needs one registered
callback, `https://auth.<zone>/oauth2/callback`, however many apps the cluster
serves. On EKS the bootstrap recipe installs what the auth host stands on
once the cluster asks for it (#2055).

## What the recipe offers

A cluster whose `oidc_auth_config` is complete (`discovery_url`, `client_id`,
`auth_proxy_host`), or whose ingress class is `nginx` or `ingress-nginx`, gets:

- `ingress-nginx`: one internet-facing NLB from the AWS Load Balancer
  Controller for every app, with TLS passed through to nginx. An NLB that
  terminates TLS itself leaves nginx redirecting to HTTPS forever. The
  controller must already run on the cluster.
- `cert-manager`, with the `letsencrypt-prod` ClusterIssuer every
  nginx-class Ingress names. It solves HTTP-01 through nginx, so it needs no
  DNS credentials, and the hosts must resolve to the NLB first.
- `oauth2-proxy`: the auth host. The install writes its Secret,
  `astrolift-central-auth` (`client-id`, `client-secret`, `cookie-secret`),
  from the cluster row before the release. The values never appear in the
  recipe, the run's result or its errors.

A cluster with neither sees the recipe it had before. external-dns must
publish nginx-class Ingresses; the recipe's own external-dns does.

## The config

`updateTenantCluster` takes `oidcAuthConfig` as one JSON object and replaces
the stored one, so send every key each time:

| Key | Value |
|---|---|
| `discovery_url` | The provider's `/.well-known/openid-configuration` URL |
| `client_id` | The app client that owns the single callback |
| `client_secret` | That client's secret |
| `cookie_secret` | 32 random bytes, URL-safe base64 (`openssl rand -base64 32 \| tr -- '+/' '-_'`) |
| `auth_proxy_host` | `auth.<zone>`, no scheme |
| `acme_email` | Optional contact for the Let's Encrypt account |

The read view reports `client_secret_set` and `cookie_secret_set`, never
the values. Keep `gateway_secret`, `logout_url` and `proxy_extra_args` in the
object if the cluster already has them.

## Order

1. Set `oidcAuthConfig` and keep the current ingress class. Nothing on the
   ALB changes.
2. Run `installClusterPrereqs` with every component the platform already
   runs on the cluster (`lastBootstrapRun.installedReleases`) plus
   `ingress-nginx`, `cert-manager` and `oauth2-proxy`. The run deletes the
   HelmRelease of every recipe component left out. Do not select a
   controller that runs outside the platform, such as a load balancer
   controller or external-dns installed with Helm by hand: a second copy
   fights the first. A dependency the run does not render is dropped rather
   than waited on. The first run can end with post-install resources
   waiting for their operator while cert-manager starts; run the same
   install again once it is Ready.
3. Check `https://auth.<zone>/ping` returns 200 with a valid certificate and
   `https://auth.<zone>/oauth2/start` redirects to the provider with the
   single callback.
4. Set `ingressClass` to `nginx`. The GitOps write-back updates
   `[ingress.auth]` in every bound app's repository.
5. Redeploy each app (`redeployApp`). Its Ingress moves to nginx, the load
   balancer controller removes its ALB rules, and external-dns repoints the
   record at the NLB.
6. Remove the per-app callbacks from the old ALB-auth client.
