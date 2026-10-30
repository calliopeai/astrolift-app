# Public install discovery

Before authentication, clients POST their `astroliftServerInfo` query to
`/app/gql/config/public/`. The prefix follows the installation's `BASE_URL`.
This endpoint serves a separate schema containing only the curated install
handshake. It has no tenant queries, mutation root or subscriptions, and uses
the existing per-IP GraphQL authentication rate, with a separate quota group
so discovery requests cannot exhaust the login endpoint's quota.

Preconditions: none for discovery itself; it is anonymous and needs only HTTP
reach to the installation. Surfaces: an HTTP POST to
`<api-url>/app/gql/config/public/`, or `astro auth status --json` for the
authenticated session's `capabilities`. Placeholders: `<api-url>` is
`api_url` from `astro auth status --json`, the installation's `BASE_URL`.

```sh
curl -s -X POST <api-url>/app/gql/config/public/ -H 'Content-Type: application/json' \
  -d '{"query":"{ astroliftServerInfo { version apiVersion installId installSlug authMethods capabilities featureFlags { key enabled } } }"}'
```

The main `/app/gql/config/` transport still requires authentication. Its legacy
transport wrapper refuses anonymous requests before resolver authorization,
even though the handshake resolver itself is public. Clients must select the
public discovery endpoint before login and the main endpoint for authenticated
session discovery and tenant operations.

The response's `apiVersion` fingerprints the full control-plane SDL, using
`schema.as_str()`, rather than the small public discovery schema. The exported
SDL has a canonical final newline; its artifact checksum differs from the
runtime fingerprint. Verify the runtime fingerprint separately when checking
a pinned mobile contract.

The anonymous discovery response contains the existing allowlisted version,
install identity, authentication methods, capabilities and feature flags.
Adding a tenant query or mutation to this schema would change its boundary and
requires a separate authorization review. Public transport tests assert its
exact root field set, private query/mutation rejection and rate limiting through
Django's real HTTP routing.

## Verify

```sh
curl -s -X POST <api-url>/app/gql/config/public/ -H 'Content-Type: application/json' \
  -d '{"query":"{ astroliftServerInfo { apiVersion capabilities } }"}'
curl -s -X POST <api-url>/app/gql/config/public/ -H 'Content-Type: application/json' \
  -d '{"query":"{ astroliftOrganizations { id } }"}'
./run.sh test core/schema/tests/test_public_handshake_transport.py
```

The first response carries `data.astroliftServerInfo.apiVersion` and
`capabilities` with no `errors`; the second returns `errors` and no tenant data;
the test run passes.
