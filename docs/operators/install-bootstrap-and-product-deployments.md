# Install bootstrap commands and product deployments

What the backend does for the installer (the Deployment Manager) at every
container start, and how it sees the Calliope AI products the installer runs
beside it. The installer itself lives in `calliope-installer`; this page covers
only the Astrolift side.

## Commands that run on every start

`backend/startup.py` runs these in order before starting uvicorn. Every one is
upsert-style or create-only and a silent no-op when its variables are unset,
so a restart never undoes an operator's later edits. A failed `migrate` stops
the start; the others log and continue.

| Command | Reads | Does |
|---|---|---|
| `showmigrations`, `migrate` | | Schema. Non-atomic migrations are re-runnable and the migrate lock no longer deadlocks concurrent index builds (#2521). |
| `collectstatic --noinput` | | Idempotent; guards against a base image older than the bake step. |
| `bootstrap_admin` | `ASTROLIFT_ADMIN_EMAIL`, `ASTROLIFT_ORG_SLUG` (default `acme`), `ASTROLIFT_ORG_NAME`, `ASTROLIFT_ORG_WEBSITE`, `ASTROLIFT_ADMIN_SUPERUSER` | Creates the organization and a pending `FirstAdministrator` for the email. No user or binding is made here: the first provider-verified sign-in with that email claims it and becomes `org_owner` (#503, #2437). Once claimed, the variable is inert. |
| `bootstrap_idp` | `ASTROLIFT_IDP_KIND` (default `oidc`), `ASTROLIFT_IDP_DISPLAY_NAME`, `ASTROLIFT_IDP_CLIENT_ID`, `ASTROLIFT_IDP_DISCOVERY_URL`, `ASTROLIFT_IDP_CLIENT_SECRET_REF`, `ASTROLIFT_IDP_IS_DEFAULT` | Upserts the `IdentityProvider` row `active_idp.json` hands to the login UI. The secret ref is a pointer, not the secret; the login path reads `AUTH0_CLIENT_SECRET`. Needs the organization from `bootstrap_admin`. |
| `bootstrap_provider_plugins` | | Upserts `ProviderPlugin` catalog rows from the in-process registry so `registerTenantCluster` passes slug validation. |
| `register_tenant_cluster` | `ASTROLIFT_CLUSTER_SLUG`, `_NAME`, `_PLUGIN_SLUG` (default `aws`), `_AUTH_METHOD` (default `exec_plugin`), `_REGION`, `_ENDPOINT`, `_CA_CERT`, `_INGRESS_CLASS`, `_ORG_SLUG` (`''` or `shared` = unscoped), and per cloud `ASTROLIFT_CLUSTER_AUTO_DISCOVER_AWS|AZURE|GCP` with their cluster identity variables | Seeds the first cluster before the first human signs in. With auto-discovery it reads endpoint and CA from `eks:DescribeCluster` + `sts:GetCallerIdentity` (AWS), the AKS managed cluster (Azure) or `container.get_cluster` (GCP). Re-running with the same slug updates the row and clears a soft delete. |
| `bootstrap_managed_domain` | `ASTROLIFT_MANAGED_DOMAIN_ZONE`, `_ZONE_ID`, `_CERTIFICATE_ARN` | Registers the install's apps zone as the shared default. Create-only. See [managed-domains.md](managed-domains.md). |
| `sync_licence_modules` | the signed licence in the task environment | Turns off, in every organization, the modules the licence withholds (#1879). A grant never turns a module on. |

Zentinelle is not connected at start; the installer runs
`manage.py connect_zentinelle` as a one-off task
([zentinelle-connect.md](zentinelle-connect.md)).

None of this needs the console; the seeded cluster shows on the Clusters
page once the first administrator has claimed the install.

## The product deployments the installer runs

The installer deploys the Workbench hub and Zentinelle onto an Astrolift
tenant cluster through its own Terraform, outside Astrolift's projects and
apps. Astrolift reads them back (#2465; design in
[../design/product-deployments-2465.md](../design/product-deployments-2465.md)).

`ReconcileProductDeploymentsWorkflow` runs every 2 minutes
(`backend/astrolift_workflows/schedule_registry.py`). It lists, on every managed
cluster, the namespaces labelled `calliope.ai/product-contract` and
`calliope.ai/product in (workbench, zentinelle)`, and keeps one
`ProductDeployment` row per namespace
(`backend/astrolift_clusters/models/product_deployment.py`): kind, deployment
name, namespace uid, `calliope.ai/managed-by`, contract version, product
version, declared and running images, endpoints, workloads, integrations,
health, `first_seen_at`, `last_seen_at`, `gone_at`, `last_scan_error`.

Reader rules (`backend/astrolift_clusters/product_deployments.py`):

- Read-only against the cluster: namespaces are listed and workload health is
  read; nothing is written there.
- A namespace carrying `astrolift.io/control-plane-scope=true` is Astrolift's
  own and is skipped, as is one being deleted.
- Contract version 1 is the only supported major today; any other version is
  listed with kind, deployment and namespace only.
- Unknown annotations are ignored. A malformed one empties its field and is
  recorded in `last_scan_error`; the row still lists.
- Rows a successful list no longer returns get `gone_at`. A list that fails
  changes nothing but `last_scan_error`.
- The event `product_deployment.changed` is emitted when a product appears,
  goes, changes health or changes images.

What is not there yet (2026-10-30): no GraphQL field and no console view
exposes `ProductDeployment` rows, and the model is not registered in the
Django admin; read the table directly or watch the `product_deployment.changed`
events.
**Secure › Zentinelle** follows the Zentinelle connection, not hosting, so a
hosted Zentinelle shows there only once connected.

## Related

- [managed-domains.md](managed-domains.md)
- [zentinelle-connect.md](zentinelle-connect.md)
- [control-plane-upgrades.md](control-plane-upgrades.md)
- [public-install-discovery.md](public-install-discovery.md)
