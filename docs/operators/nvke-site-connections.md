# NVKE sites and connections

An NVKE site belongs to one Astrolift organization. Its organization namespace
is immutable. Removing a site soft-deletes its local record and retains its
history; it does not destroy upstream infrastructure. Outstanding operations,
including uncertain outcomes, must be resolved before removal.

## Permissions

| Permission | Authority |
|---|---|
| `nvke.read` | Read the organization's site/connection metadata |
| `nvke.manage` | NVKE workload lifecycle authority, separate from site credentials |
| `nvke.admin` | Create/configure/disable/remove sites and configure credentials |

These permissions are organization-scoped. A team or project grant does not
authorize an organization-owned site. The active account must remain a member
of the organization, or be the platform operator. Another organization's site
ID and an unknown site ID produce the same result.

Organization owners and admins receive the explicit permissions through the
role migration. Custom roles can grant them separately. The existing `admin`
bearer scope covers them; ordinary cluster bearer scopes do not carry NVKE
authority.

## Interfaces

Configure each required interface separately:

| Surface | Endpoint base | Purpose |
|---|---|---|
| `CONTROLLER` | Includes `/api`, for example `https://nvke.example/api` | Existing controller REST operations |
| `TENANT` | Base hosting `/v1/clusters` | Declarative workload-cluster public API |
| `OPERATOR` | Base hosting `/v1/machine-images` and related resources | Public artifact and class APIs |
| `DATASTORE` | Management Kubernetes API server base | Explicit management resource and access operations |

The pinned tenant public API is a PoC with a deployment-selected namespace.
Its deployment/proxy must be scoped to the organization namespace recorded on
the site. Do not treat it as an authenticated multi-tenant service by itself.
Astrolift's direct datastore adapter binds the namespace from the trusted site
mapping. A failed public API call never silently switches to datastore credentials.

Endpoint URLs cannot contain credentials, query strings, fragments or path
traversal. HTTPS is the default. Plain HTTP requires the explicit `allowHttp`
setting for an evaluation endpoint. The connection timeout is bounded to
1–300 seconds. Saving a connection clears its prior verification state;
configuration alone does not prove reachability or remote readiness.

## GraphQL configuration

Create a site:

```graphql
mutation {
  createNvkeSite(input: {
    name: "GPU site"
    slug: "gpu-site"
    organizationNamespace: "org-owned"
  }) {
    ok
    errors { code message }
    data { id version }
  }
}
```

Configure a connection with the returned site ID/version:

```graphql
mutation ConfigureConnection($input: ConfigureNvkeConnectionInput!) {
  configureNvkeConnection(input: $input) {
    ok
    errors { code message }
    data {
      id
      version
      connections { id version surface endpoint hasCredentials verifiedAt }
    }
  }
}
```

The input includes `siteId`, `expectedSiteVersion`, `surface`,
`expectedConnectionVersion` (`0` for a new connection), `endpoint`, and optional
`credentials`, `allowHttp` and `timeoutSeconds`. The credential object accepts
`token`, `caPem`, `clientCertificatePem` and `clientKeyPem`. Supply the client
certificate and private key together.

- Omit `credentials`, or pass `null`, to preserve the existing credential bundle.
- Pass a new credential object to replace the bundle, including on rotation.
- Pass an empty credential object to clear it.

Credentials are encrypted with the platform's secrets-at-rest backend. API
responses contain only `hasCredentials`, never the credential bundle or its
ciphertext. Audit redaction follows schema positions, including nested variables
and rejected endpoint URLs containing embedded credentials.

Every connection configuration change advances both its own version and the
site version. Refresh after a `VERSION_MISMATCH` response instead of resubmitting
an old configuration. Site enable/disable and removal also require the current
site version.

Read sites with cursor pagination:

```graphql
query Sites($after: String) {
  astroliftNvkeSites(after: $after, limit: 50) {
    items { id version name slug isActive connections { surface hasCredentials } }
    nextCursor
    totalCount
  }
}
```

Use `astroliftNvkeSite(siteId: ...)` for one site,
`setNvkeSiteEnabled(input: ...)` to disable/re-enable it, and
`removeNvkeSite(input: ...)` to remove the local registration. Site configuration
does not start remote cluster provisioning.
