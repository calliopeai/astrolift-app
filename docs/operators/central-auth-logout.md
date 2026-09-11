# Central auth logout

For managed app hosts using `nginx` or `ingress-nginx`, configure the cluster's
optional `oidc_auth_config.logout_url` with the provider's complete **browser
logout URL**. Apps can then send the browser to `/auth/logout` after clearing
their own application session, including with `AUTH_LOGOUT_PATH=/auth/logout`.
When enabled, that exact path is reserved at the edge. Applications with their
own logout handler at the same path must move their session-clearing action to
a different route before redirecting to the platform logout route.

The browser visits:

1. The app's `/auth/logout`, then the central auth host's `/auth/logout`.
2. The central proxy's `/oauth2/sign_out?rd=%2Fauth%2Fend-session`, which expires
   the proxy session cookie and its chunks on the configured cookie domain.
3. The auth host's `/auth/end-session`, which redirects to `logout_url`.
4. The provider's registered return URL. The central host supplies
   `/auth/logged-out`, a public signed-out page that does not start a new login.

All platform destinations are fixed configuration. Request query parameters
cannot change them. The proxy's `rd` is local to the auth host, so its existing
redirect allowlist does not need the provider domain. See the
[oauth2-proxy sign-out contract](https://oauth2-proxy.github.io/oauth2-proxy/7.6.x/features/endpoints/).

## Cognito setup

Use the client that the **central auth proxy** actually uses. Its ID can differ
from an older ALB-auth client, and the Cognito issuer discovery URL does not
identify its managed-login domain. Configure both explicitly rather than
deriving them from the cluster's legacy `alb_auth_config`.

For an auth host `auth.apps.example.com`:

1. Register `https://auth.apps.example.com/auth/logged-out` as an **Allowed
   sign-out URL** on that Cognito app client. One registration serves all the
   managed apps covered by the central cookie.
2. Set `logout_url` to the Cognito domain's `/logout` endpoint with that
   `client_id` and URL-encoded `logout_uri`. For example:

   ```text
   https://tenant.auth.us-west-2.amazoncognito.com/logout?client_id=CENTRAL_CLIENT_ID&logout_uri=https%3A%2F%2Fauth.apps.example.com%2Fauth%2Flogged-out
   ```

Cognito requires the registered sign-out URL. Its logout endpoint ends the
Cognito managed-login session; it does not end an upstream OIDC or social IdP
session. Federated SAML logout depends on that provider's SLO configuration.
See [Cognito logout](https://docs.aws.amazon.com/cognito/latest/developerguide/logout-endpoint.html).

## Apply and verify

`updateTenantCluster` accepts `logout_url` inside its existing `oidcAuthConfig`
JSON object and requires `cluster.update`. Supply the complete intended config:
the mutation replaces that object. In particular, retain the existing cookie
and gateway secrets; the API's redacted read view cannot be written back as a
complete replacement. The public read view and `[ingress.auth]` GitOps write-back
include the logout URL, which must contain only public routing parameters, never
tokens or secrets. Fixed HTTPS URLs are supported; per-session token templates
are not.

For startup registration, supply `ASTROLIFT_CLUSTER_OIDC_LOGOUT_URL` alongside
the existing `ASTROLIFT_CLUSTER_OIDC_*` registration settings. Re-registration
preserves an operator-set logout URL when that environment variable is absent.
An explicitly supplied nonempty value replaces it. Invalid URLs are rejected
before saving the cluster.

After saving the cluster config, apply the refreshed `oauth2-proxy` bootstrap
recipe, then call `reconcileClusterIngresses` with `cluster.manage` or redeploy
the apps. The recipe puts the three routes on the auth host's ingress; saving
the cluster row alone does not update that Helm release. The nginx controller
must accept `configuration-snippet` annotations, as required for the platform's
gateway identity headers. Reconciliation preserves app-owned snippet content
and regenerates the logout route and declared identity headers in the existing
platform fence.

Verify in a browser that logout clears the proxy cookies, reaches the provider
logout endpoint, and lands on `/auth/logged-out`. Revisit the app and verify the
expected account-selection behavior for that provider. A paused app still
returns its pause response; use the central host's `/auth/logout` directly.

To disable the routes, clear `logout_url` through the cluster mutation, refresh
the proxy recipe and reconcile the ingresses. Keep the other OIDC fields to
retain authentication. No database migration is required. Clusters without
`logout_url` retain their existing behavior.

This flow covers managed subdomains using the central proxy. Custom domains
with their own proxy, ALB authentication, application-owned sessions, and
one-time removal of legacy `AWSELBAuthSessionCookie-*` cookies have separate
logout/migration requirements tracked in #1727.
