# Calliope AI product deployments on Astrolift clusters (design, #2465)

Status: **design for review, 2026-10-18.** Nothing here is approved or built. No
product code ships with this document.

Inputs: #2465, the installer contract
`calliope-installer/docs/contracts/astrolift-product-deployments.md` (version 1,
drafted with this design), calliope-installer#389, #443, #508, #447 (minimal
RBAC), #2331 (Astrolift serves and gates Calliope AI products).

## 1. Problem

The Deployment Manager runs the Workbench hub and Zentinelle on an Astrolift
tenant cluster through Terraform (`terraform/k8s/workbench-serving`,
`terraform/k8s/zentinelle-serving`). They create their own namespaces and
workloads and never pass through Astrolift's projects or apps.

What Astrolift shows for them today, from the code: most likely nothing. The
cluster Status tab's `cluster_workload_health_dispatch` reads the bound app
namespaces and falls back to `astrolift-system`; nothing reads the `junohub` or
`zentinelle` namespaces. calliope-installer#389's second-deployment run
(`conflict-two`) confirms this on a live cluster and posts on #2465.

The only Zentinelle concept is `ZentinelleConnection`
(`astrolift_operations/models/zentinelle_connection.py`), a link to a Zentinelle
reached by URL and an enrollment code. Nothing models the Workbench.

## 2. Decision: how Astrolift learns of them

**Recommended: Astrolift discovers labelled namespaces.** The installer writes a
versioned set of labels and annotations on each product namespace in the same
apply that creates it; Astrolift's scheduled cluster reconcile reads them. The
alternative, the installer calling an Astrolift API after apply, is not taken.

Why, from the code:

1. **Reads already reach every namespace; writes cannot reach these.** Under the
   minimal RBAC contract (`backend/deploy/rbac/control-plane-minimal.yaml`) the
   control plane holds cluster-wide `get/list/watch` on namespaces, Deployments,
   ReplicaSets and pods, and writes only in namespaces labelled
   `astrolift.io/control-plane-scope=true`. The contract forbids that label on
   product namespaces, so "read-only from Astrolift" is enforced by admission,
   not just by our code. Under the full platform ClusterRole the same reads
   exist.
2. **The writer stays single.** The Deployment Manager is the only writer of the
   deployment (installer `docs/deployment-lifecycle.md`, R1). Labels live on an
   object it already writes, so it writes nothing new anywhere else. An API call
   would make it a second writer of Astrolift's database, holding an Astrolift
   org credential with write scope behind central auth, which no agent holds
   today.
3. **No drift between record and reality.** The labels are created, changed and
   destroyed in the same Terraform plan as the workload. A registration call can
   fail after a good apply, be skipped on teardown, or describe an image that
   never rolled out; a namespace cannot outlive its own deletion.
4. **The machinery exists.** `reconcile_cluster_capabilities`
   (`astrolift_workflows/activities/scheduled.py`, every 10 minutes, managed
   clusters only) already walks every cluster through the driver, and
   `cluster_workload_health_dispatch` already reads per-Deployment health for a
   namespace list. One driver read is new: namespaces by label selector.
5. **It holds offline.** Air-gapped installs and installs where the agent cannot
   reach Astrolift's API need nothing extra.

What it costs, and the answer:

| Cost | Answer |
|---|---|
| Latency up to one scan interval | A 2-minute product scan (section 5) and a `refreshClusterProductDeployments` mutation for the operator who just applied. |
| Anyone who can create a namespace can forge the labels | Only the cluster owner can: tenants reach the cluster through Astrolift, whose namespaces carry `control-plane-scope` and are ignored. Labels drive display only. The one action they feed (Zentinelle connect, section 7) still needs Zentinelle's own enrollment handshake, which a forged namespace cannot pass. |
| A label contract to keep in step across two repos | Versioned by `calliope.ai/product-contract`; canonical copy in calliope-installer; N and N-1 read here (section 4). |
| Unmanaged clusters are never seen | Correct: only `lifecycle = managed` clusters are scanned, as for capabilities. |

## 3. The Deployment primitive

A **product deployment** is a Calliope AI product the Deployment Manager runs on
an Astrolift cluster: one namespace, one product kind.

| Field | Source | Notes |
|---|---|---|
| `kind` | label `calliope.ai/product` | `workbench` or `zentinelle` |
| `deployment_name` | label `calliope.ai/deployment` | the install's deployment name |
| `managed_by` | label `calliope.ai/managed-by` | `deployment-manager`; shown as "Managed by the Deployment Manager" |
| `contract_version` | label `calliope.ai/product-contract` | integer |
| `namespace`, `namespace_uid` | the Namespace | uid tells a reinstall from the same install |
| `images` (declared) | annotation `calliope.ai/images` | component to image reference |
| `running_images` (observed) | pods' `containerStatuses[].imageID` | the resolved digest per component |
| `version` | annotation `calliope.ai/version` | reserved in contract 1; null until written |
| `endpoints` | annotation `calliope.ai/endpoints` | hub URL; Zentinelle console and API |
| `workloads` | annotation `calliope.ai/workloads` | Deployments health is read from |
| `health` | derived | `healthy` (every workload ready = desired), `degraded` (some ready), `down` (a workload at zero ready), `unknown` (cluster offline, scan failed) |
| `project` | **Astrolift-owned** | section 6 |
| `zentinelle_connection` | **Astrolift-owned** | section 7 |
| `first_seen_at`, `last_seen_at`, `gone_at` | the scan | `gone_at` set when a successful scan no longer finds the namespace |

Read-only from Astrolift: no field the cluster supplies is editable here; no
mutation reaches the cluster. The two Astrolift-owned fields are Astrolift's own
records about the deployment, not changes to it.

### Model

`ProductDeployment(BaseCoreModel)` in `astrolift_clusters/models/product_deployment.py`:
`cluster` (FK `TenantCluster`, cascade), the fields above, `project` (FK
`astrolift_identity.Project`, null, `SET_NULL`), `zentinelle_connection` (FK
`astrolift_operations.ZentinelleConnection`, null, `SET_NULL`), `last_scan_error`.
Unique live row per `(cluster, namespace)`. A namespace recreated under the same
name (new uid, same kind and deployment) keeps its row, so a reinstall keeps its
project link.

The organization is not stored: it is `cluster.organization`, or for a shared
cluster `project.organization` once assigned (section 8).

## 4. The contract (summary)

Canonical: `calliope-installer/docs/contracts/astrolift-product-deployments.md`.

- Selector: `calliope.ai/product-contract,calliope.ai/product in (workbench,zentinelle)`.
- Labels: `calliope.ai/product-contract` (`"1"`), `calliope.ai/product`,
  `calliope.ai/deployment`, `calliope.ai/managed-by`.
- Annotations: `calliope.ai/images`, `calliope.ai/endpoints`,
  `calliope.ai/workloads` (JSON or comma lists).
- Reserved, not yet written: `calliope.ai/version`,
  `calliope.ai/astrolift-project`, `endpoints.api_internal`.
- Never: `astrolift.io/control-plane-scope`, secrets.

Reader rules here:

- Ignore a matching namespace that carries `astrolift.io/control-plane-scope=true`.
- Ignore unknown annotations and unknown JSON keys.
- Read majors N and N-1. A namespace on any other major is listed with kind,
  deployment and namespace and `contractSupported = false`, nothing else parsed.
- Malformed JSON in one annotation empties that field and records
  `last_scan_error`; the row still lists.

## 5. Discovery and refresh

A new scheduled activity `reconcile_product_deployments`
(`ScheduleKind.RECONCILE_PRODUCT_DEPLOYMENTS`, every 2 minutes), separate from
the capability probe so a slow probe does not hold it:

1. For each managed, active cluster whose heartbeat is not `offline`:
   `list_namespaces(label_selector=...)` (new driver protocol method on the SDK,
   k8s_native, EKS, AKS and GKE drivers; namespaces `list` is already granted
   under both RBAC modes).
2. Upsert one row per namespace; set `gone_at` on rows not returned by a
   successful list. A failed list changes no row except `last_scan_error`.
3. `cluster_workload_health_dispatch(cluster, namespaces=[...product namespaces])`
   for health, and pod `imageID`s for `running_images`.
4. Emit `PRODUCT_DEPLOYMENT_CHANGED` on appear, disappear, health change or image
   change, through the existing event writer, so notifications and audit see it.
5. Run the Zentinelle link step (section 7).

`backend/core/tests/test_control_plane_rbac_contract.py` gains the namespace list
call, which the minimal file already grants.

The cluster Status tab's workload table threads the product namespaces in too,
tagged by product, and app counts leave them out.

## 6. The project link

A Workbench or Zentinelle deployment belongs to at most one project, so it shows
beside that project's apps and agents and inherits its access.

**Recommended: the link is Astrolift's, set in Astrolift.** The installer does
not know Astrolift's projects (GUIDs made at run time), and a project is an
Astrolift access boundary that only Astrolift should draw. An operator with
rights on both the cluster and the project assigns it
(`assignProductDeploymentProject`). The reserved annotation
`calliope.ai/astrolift-project` may later seed the link when it is unset; it
never overrides one made here.

Inheriting access means: a project member with `project.read` sees the
deployment, its health and its links in the project view. It does not grant
access to the product itself; the hub and Zentinelle keep their own sign-in.

## 7. Wiring Zentinelle

An in-cluster Zentinelle should satisfy the organization's `ZentinelleConnection`
without the operator typing its URL. Connecting today
(`zentinelle_connect.connect`) exchanges a one-time enrollment code for the
install credential and is behind `zentinelle.connect` and step-up elevation. The
labels can say where Zentinelle is; they cannot carry the code, and should not.

Per discovered Zentinelle deployment, the link step decides one state:

| State | When | What happens |
|---|---|---|
| `connected` | the owning connection's `base_url` matches `endpoints.api` (normalized) | link the row; the Zentinelle screen says "in-cluster, on <cluster>" |
| `available` | no connection exists for the owner | the Zentinelle screen shows "Zentinelle found on <cluster>" with Connect prefilled: URL from `endpoints`, gateway URL from `endpoints.api_internal` once the contract carries it. The admin pastes the enrollment code and passes step-up. |
| `other_connection` | a connection exists to a different Zentinelle | show both; never rewire on our own |

The owner is the cluster's organization; for a shared cluster it is the install
connection (`organization` null), which only the platform operator connects
(#1905).

This removes the hand-typed URL, not the handshake. Removing the handshake too
(section 10, Q3) needs a Zentinelle-side bootstrap path; until it exists, one
confirm with a code is the honest floor.

## 8. API

Strawberry types follow `astrolift_operations` and `astrolift_clusters`: an
`Astrolift` prefix on the GraphQL name, a `*Type` class, a `*_to_type`
converter, `@require_permission` with a scope factory, then `@tenant_scoped()`.

### Types

```graphql
enum AstroliftProductKind { WORKBENCH ZENTINELLE }
enum AstroliftProductHealth { HEALTHY DEGRADED DOWN UNKNOWN }
enum AstroliftProductZentinelleLinkState { CONNECTED AVAILABLE OTHER_CONNECTION }

type AstroliftProductDeployment {
  id: GUID!
  kind: AstroliftProductKind!
  deploymentName: String!
  namespace: String!
  clusterId: GUID!
  clusterSlug: String!
  clusterName: String!
  project: AstroliftProductDeploymentProject       # id, slug, name; null when unassigned
  managedBy: String!                               # "deployment-manager"
  contractVersion: Int!
  contractSupported: Boolean!
  version: String                                  # null until the contract carries it
  images: [AstroliftProductImage!]!                # component, declared, running (digest or null)
  endpoints: [AstroliftProductEndpoint!]!          # name, url
  health: AstroliftProductHealth!
  workloads: [AstroliftProductWorkload!]!          # name, desiredReplicas, readyReplicas, restartCount24h
  zentinelleLink: AstroliftProductZentinelleLink   # Zentinelle only: state, connection base URL
  firstSeenAt: DateTime!
  lastSeenAt: DateTime
  goneAt: DateTime
}
```

No field carries a secret; endpoints are the URLs the products already publish.

### Queries

| Field | Permission and scope | Rows |
|---|---|---|
| `astroliftClusterProductDeployments(clusterId: GUID!, includeGone: Boolean = false)` | `cluster.read`, `cluster_org_scope(CLUSTER_READ)` | the cluster's rows, cluster found through `_clusters_qs` (caller's org plus shared) |
| `astroliftProjectProductDeployments(projectId: GUID!)` | `project.read`, `project_scope_by_guid("project_id", permissions=(PROJECT_READ,))` | rows with that project, live only |
| `astroliftProductDeployment(id: GUID!)` | `cluster.read`, org scope of the row's cluster | one row or null when not visible |
| `astroliftProductDeploymentsPage(filter, sort, page, pageSize)` | `cluster.read`, `cluster_catalog_org_scope(CLUSTER_READ)` | org-wide list on the list contract (`astrolift_graphql/README.md`); filters `kind`, `cluster`, `project`, `health`; sorts `name`, `kind`, `health`, `lastSeen`. For the astro CLI (`astro products ls`) and Chat Studio. |

Tenancy, fail closed as `_clusters_qs` does (null org returns nothing):

- Org-owned cluster: visible to that organization.
- Shared cluster: visible to the platform operator (`check_platform_operator`),
  and to an organization only once assigned to one of its projects. A shared
  cluster is visible to every org; its products' hub URLs are not.

Reads reuse `cluster.read` and `project.read`: a product deployment is a fact
about a cluster, and a new permission would change every role matrix for no new
boundary. Q5 records the alternative.

### Mutations (Astrolift's own records only)

| Mutation | Permission | Effect |
|---|---|---|
| `assignProductDeploymentProject(input: {id, projectId})` (`projectId` null clears) | `project.update` on the target project and `cluster.update` on the cluster; platform operator on a shared cluster; step-up, as it widens who sees the deployment | sets `project`; audit row |
| `refreshClusterProductDeployments(clusterId)` | `cluster.update`, `cluster_org_scope` | runs the scan for one cluster now |

Neither reaches the cluster.

### MCP

`astrolift_list_product_deployments` beside the existing read tools in
`astrolift_agents/views/mcp.py`, scoped like the page query, so Chat Studio lists
them with the caller's own Astrolift token.

## 9. Display

Standing rules: Storybook first, every component and whole screen; no long
pages, each view fits about two screens and fetches only what is visible.

| Surface | What | Where |
|---|---|---|
| Cluster detail, Status tab | A **Calliope AI products** card above the workload table: up to five rows (kind mark, deployment name, health dot, version or short digest, Open hub / Open console), "View all" past five. Distinct from user apps: own card, a "Managed by the Deployment Manager" tag, no deploy, scale or delete actions. Workload table rows in product namespaces carry the product tag. | `components/screens/clusters/products/ClusterProductsCard.tsx` |
| Project detail | A **Calliope AI products** section between agents and apps, rendered only when the project has one. Same row component. | `components/screens/projects/ProjectProductDeployments.tsx` |
| Detail | A side sheet, not a page: images declared and running (drift flagged), workloads with ready counts, endpoints, contract version, Zentinelle link state, project assignment (when permitted), first and last seen. | `components/screens/clusters/products/ProductDeploymentSheet.tsx` |
| Zentinelle screen | "Zentinelle found on <cluster>" banner with prefilled Connect, or "in-cluster, on <cluster>" on a matched connection. | `components/screens/secure/ZentinelleConnection.tsx` |

Stories, written before the screens are wired: healthy, degraded, down, unknown
(cluster offline), unsupported contract, gone, no endpoints, shared cluster
unassigned (operator view), Zentinelle connected / available / other connection,
drift between declared and running image, the card with six rows ("View all").

## 10. Open questions

1. **Scan cadence.** 2 minutes per managed cluster is cheap (one selector list,
   one Deployment list per product namespace). Confirm against the services shard
   and fleet size, or fold into the 10-minute capability tick with the refresh
   mutation as the fast path.
2. **Project link source.** Astrolift-side assignment is recommended. Should the
   install record also carry an Astrolift project (written as
   `calliope.ai/astrolift-project`) for installs that want it pre-assigned?
3. **Zentinelle without a code.** Full auto-connect needs a credential Astrolift
   can trust from the cluster: for example the installer, at Zentinelle's
   bootstrap, mints a one-time enrollment code into a Secret in the Zentinelle
   namespace that Astrolift reads (it already reads Secrets cluster-wide) and
   exchanges once, behind an install-record answer. That needs a Zentinelle
   ticket for a bootstrap-time code and a decision that the installer operator's
   answer stands in for step-up.
4. **In-cluster gateway URL.** Gateways in `astrolift-system` would reach
   Zentinelle at `zentinelle-backend.<ns>.svc.cluster.local`; the backend's
   `ALLOWED_HOSTS` does not admit that name today. Zentinelle or the installer
   adds it, then the contract publishes `endpoints.api_internal` (additive).
5. **Permissions.** Reuse `cluster.read` / `project.read` (recommended), or add
   `product_deployment.read` if hub URLs should be narrower than cluster
   visibility.
6. **Gone rows.** Keep with `gone_at` forever, or soft-delete after 30 days? A
   kept row preserves the project link across a teardown and reinstall.
7. **Other runtimes.** Workbench on AKS and GKE applies `workbench-serving` as a
   root; its namespace carries the contract too, but Astrolift sees it only when
   that cluster is a managed Astrolift tenant cluster.
8. **Today's display.** Confirm with calliope-installer#389 (`conflict-two`)
   what the UI and API show for these namespaces now, and whether anything is
   misleading enough to fix before this lands.

## 11. Delivery order

1. Contract version 1 in calliope-installer (drafted with this document).
2. Storybook: row, card, sheet, project section, Zentinelle banner, every state.
3. Model, driver `list_namespaces`, scan activity and schedule, tests (RBAC
   contract test, tenancy tests for shared clusters).
4. GraphQL types, queries, mutations; schema regeneration; MCP read tool.
5. Wire the screens; Zentinelle link step.
6. Validate on CONFLICT after the installer change is applied there.
