import { gql } from "@apollo/client";

export const REGISTER_TENANT_CLUSTER = gql`
  mutation RegisterTenantCluster($input: RegisterTenantClusterInput!) {
    registerTenantCluster(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        providerPluginSlug
        region
        authMethod
        ingressClass
        isActive
      }
    }
  }
`;

export const UNREGISTER_TENANT_CLUSTER = gql`
  mutation UnregisterTenantCluster($input: UnregisterTenantClusterInput!) {
    unregisterTenantCluster(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const CREATE_MANAGED_DOMAIN = gql`
  mutation CreateManagedDomain($input: CreateManagedDomainInput!) {
    createManagedDomain(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        zone
        dnsDriver
        defaultFor
        isWildcardManaged
      }
    }
  }
`;

export const SOFT_DELETE_MANAGED_DOMAIN = gql`
  mutation SoftDeleteManagedDomain($input: SoftDeleteManagedDomainInput!) {
    softDeleteManagedDomain(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const REVALIDATE_MANAGED_DOMAIN = gql`
  mutation RevalidateManagedDomain($clusterId: GUID!, $zone: String!) {
    revalidateManagedDomain(clusterId: $clusterId, zone: $zone) {
      ok
      errors {
        code
        message
        field
      }
      data {
        zone
        signaled
        message
      }
    }
  }
`;

// Tenant-scoped count of active TenantCluster rows in the caller's
// org. Used by the /apps/new wizard to gate Step 1 — if it's zero,
// we refuse to walk the operator into a wizard whose deploy can't
// land. Backed by the registry's PRECONDITION gate on registerApp
// (#315), so this is a UX shortcut, not the source of truth.
export const CLUSTER_COUNT = gql`
  query ClusterCount {
    astroliftClusterCount
  }
`;

export const LIST_CLUSTERS = gql`
  query ListClusters {
    astroliftClusters {
      id
      slug
      name
      organizationSlug
      providerPluginSlug
      region
      endpoint
      authMethod
      ingressClass
      albAuthConfig
      oidcAuthConfig
      isActive
      capabilities
      capabilitiesProbedAt
      createdAt
      lifecycle
      lastManagementError
      managedAt
      lastHeartbeatAt
      heartbeatIntervalSeconds
      heartbeatStatus
      heartbeatAgeSeconds
      agentProvisioned
      lastBootstrapRun {
        id
        status
        chartVersion
        installedReleases
        cliVersion
        errorMessage
        startedAt
        endedAt
        triggeredByUsername
      }
    }
  }
`;

/**
 * Cursor-paginated companion to ``LIST_CLUSTERS`` (#1230).
 *
 * Speaks the platform page envelope — ``{ items, nextCursor, totalCount }``
 * out, ``limit`` + ``after`` in — so ``useCursorTable`` walks the whole
 * fleet on the server instead of the surface fetching every cluster and
 * filtering the result in the browser. ``search`` is the field's only
 * filter argument; it takes no sort argument, so a table over it declares
 * no ``sortVariable`` and no ``Column.sortKey``.
 *
 * ``$limit: Int`` is deliberately nullable against the schema's
 * ``limit: Int! = 50`` — the argument carries a default, which is what
 * makes a nullable variable legal there, and the controller always sends
 * a value anyway.
 *
 * The row selection is a spreadable fragment rather than a plain
 * template-literal constant because this file IS in the codegen document
 * set: ``graphql-tag-pluck`` cannot resolve a bare ``${FIELDS}``
 * interpolation and (with ``noSilentErrors``) fails the run. ``LIST_CLUSTERS``
 * keeps its inline copy for /ops, /providers, the fleet map and the admin
 * metrics; the cluster detail and its tabs read ``GET_CLUSTER`` (#2150).
 *
 * The list contract (spec 44 §5.1, #2150): ``filter`` (provider, status,
 * live, registeredBy), ``sort`` (``-lastProbe,name``) and ``page`` /
 * ``pageSize``. Any of the last three selects numbered paging on the
 * server: an exact ``totalCount``, ``page`` and ``pageSize`` echoed.
 */
const CLUSTER_FIELDS = gql`
  fragment ClusterFields on AstroliftTenantCluster {
    id
    slug
    name
    organizationSlug
    providerPluginSlug
    region
    endpoint
    authMethod
    ingressClass
    albAuthConfig
    isActive
    capabilities
    capabilitiesProbedAt
    createdAt
    lifecycle
    lastManagementError
    managedAt
    lastHeartbeatAt
    heartbeatIntervalSeconds
    heartbeatStatus
    heartbeatAgeSeconds
    agentProvisioned
    createdByUsername
    lastBootstrapRun {
      id
      status
      chartVersion
      installedReleases
      cliVersion
      errorMessage
      startedAt
      endedAt
      triggeredByUsername
    }
  }
`;

export const LIST_CLUSTERS_PAGE = gql`
  ${CLUSTER_FIELDS}
  query ListClustersPage(
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftClustersListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftClustersPage(
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...ClusterFields
      }
      nextCursor
      totalCount
      page
      pageSize
    }
  }
`;

/**
 * One cluster by slug (#2150): the detail page and every cluster tab.
 * They used to find their cluster in ``LIST_CLUSTERS``, which stops at 200
 * rows, so a cluster past the 200th read as "not found" on its own page.
 * Null when the caller cannot see the cluster.
 */
export const GET_CLUSTER = gql`
  ${CLUSTER_FIELDS}
  query GetCluster($slug: String!) {
    astroliftCluster(slug: $slug) {
      ...ClusterFields
      oidcAuthConfig
    }
  }
`;

export const UPDATE_TENANT_CLUSTER = gql`
  mutation UpdateTenantCluster($input: UpdateTenantClusterInput!) {
    updateTenantCluster(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        ingressClass
        albAuthConfig
        oidcAuthConfig
      }
    }
  }
`;

// Push the cluster's current alb_auth_config onto every live
// managed-subdomain Ingress (#851). updateTenantCluster only changes
// what the next deploy renders; this applies (or removes) the
// alb.ingress.kubernetes.io/auth-* annotations on the running
// Ingresses now. The settings card fires updateTenantCluster first
// (to persist config) then this, and surfaces reconciledCount in the
// success toast.
export const RECONCILE_CLUSTER_INGRESSES = gql`
  mutation ReconcileClusterIngresses($input: ReconcileClusterIngressesInput!) {
    reconcileClusterIngresses(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        reconciledCount
        skippedCount
        errors
      }
    }
  }
`;

// Bootstrap-run history for a single cluster (#319). The 'View
// history' disclosure on the Last bootstrap card fans this query
// out when expanded; we don't pull the full history on the cluster
// list to keep that query light.
export const CLUSTER_BOOTSTRAP_RUNS = gql`
  query ClusterBootstrapRuns($slug: String!, $limit: Int) {
    astroliftCluster(slug: $slug) {
      id
      slug
      bootstrapRuns(limit: $limit) {
        id
        status
        chartVersion
        installedReleases
        cliVersion
        errorMessage
        startedAt
        endedAt
        triggeredByUsername
      }
    }
  }
`;

// CLI report-back of an ``astro cluster bootstrap`` outcome (#319).
// Operator-side surfaces don't fire this — the CLI does. Exposed
// from the queries module so the type generation picks it up and
// makes the mutation discoverable from the typed client.
export const RECORD_CLUSTER_BOOTSTRAP_RUN = gql`
  mutation RecordClusterBootstrapRun($input: RecordClusterBootstrapRunInput!) {
    recordClusterBootstrapRun(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
      }
    }
  }
`;

// Bring-into-management (#316). Returns the cluster row in
// "managing" state; the list view polls LIST_CLUSTERS to render
// the transition.
// Decommission path. `deleteCloudInfra: false` (default) only lifts
// the platform RBAC bundle; the underlying EKS/GKE/AKS cluster stays
// running and operator-owned. `deleteCloudInfra: true` ADDITIONALLY
// calls the driver's teardown_cluster which deletes the cloud-
// managed cluster (node groups, Fargate profiles, etc.). Surfaced in
// the UI as an explicit "danger zone" checkbox.
export const DECOMMISSION_CLUSTER = gql`
  mutation DecommissionCluster($input: DecommissionClusterInputType!) {
    decommissionCluster(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        lifecycle
        lastManagementError
      }
    }
  }
`;

export const BRING_CLUSTER_INTO_MANAGEMENT = gql`
  mutation BringClusterIntoManagement($input: BringClusterIntoManagementInputType!) {
    bringClusterIntoManagement(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        lifecycle
        lastManagementError
        managedAt
      }
    }
  }
`;

// Refresh path — same workflow, accepts already-managed clusters.
// ``forcePreflight`` re-runs the preflight Job; default false skips
// it for a fast probe + RBAC reconcile.
export const REFRESH_CLUSTER_MANAGEMENT = gql`
  mutation RefreshClusterManagement($input: RefreshClusterManagementInputType!) {
    refreshClusterManagement(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        lifecycle
        lastManagementError
        managedAt
      }
    }
  }
`;

export const LIST_MANAGED_DOMAINS = gql`
  query ListManagedDomains {
    astroliftManagedDomains {
      id
      zone
      organizationSlug
      dnsDriver
      defaultFor
      isWildcardManaged
      createdAt
      provisionState
      provisionNameservers
      provisionValidationRecords
      delegationCheck
      provisionClusterId
    }
  }
`;

// Driver-backed pod-phase rollup + recent Warning events (#68 slice 1).
// Drives the Live-health card on the Status tab.
export const CLUSTER_HEALTH = gql`
  query ClusterHealth($clusterId: GUID!, $eventLimit: Int) {
    astroliftClusterHealth(clusterId: $clusterId, eventLimit: $eventLimit) {
      clusterId
      pods {
        namespace
        phase
        count
      }
      events {
        namespace
        name
        reason
        message
        type
        count
        firstSeen
        lastSeen
        involvedObject
      }
    }
  }
`;

// Cheap keep-alive liveness snapshot (#808). Reads only persisted
// heartbeat fields — NO driver / Prometheus / Temporal call — so it
// returns instantly even when the apiserver is unreachable. This is
// the query the Status tab hits FIRST to decide whether to render the
// live cards or the targeted 'cluster offline' empty-state.
export const CLUSTER_LIVE_STATE = gql`
  query ClusterLiveState($clusterId: GUID!) {
    astroliftClusterLiveState(clusterId: $clusterId) {
      clusterId
      status
      lastHeartbeatAt
      heartbeatAgeSeconds
      heartbeatIntervalSeconds
      agentProvisioned
      nodeCount
      nodeReadyCount
      cpuUtilization
      memoryUtilization
      podTotal
      podsByNamespace
      appReadiness
      ingressIps
      agentVersion
    }
  }
`;

// Issue (or rotate) the in-cluster keep-alive agent key (#808). Returns
// the raw scoped key EXACTLY ONCE — the settings UI shows it for the
// operator to paste into the agent's Secret, then it's unrecoverable.
export const ISSUE_CLUSTER_AGENT_KEY = gql`
  mutation IssueClusterAgentKey($input: IssueClusterAgentKeyInput!) {
    issueClusterAgentKey(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        clusterId
        agentKey
        intervalSeconds
        heartbeatUrl
        rotated
      }
    }
  }
`;

// Recent Temporal workflow runs targeting this cluster (#394). Pulled
// live from Temporal's visibility API; empty when Temporal is
// disabled or the query fails.
export const RECENT_CLUSTER_WORKFLOWS = gql`
  query RecentClusterWorkflows($clusterId: GUID!, $limit: Int) {
    astroliftRecentClusterWorkflows(clusterId: $clusterId, limit: $limit) {
      workflowId
      workflowType
      status
      startedAt
      closedAt
      runId
    }
  }
`;

// Cluster-scoped slice of the mutation audit log (#68 slice 2). Drives
// the Lifecycle timeline card on the Status tab.
export const CLUSTER_LIFECYCLE_AUDIT = gql`
  query ClusterLifecycleAudit($clusterId: GUID!, $limit: Int) {
    astroliftClusterLifecycleAudit(clusterId: $clusterId, limit: $limit) {
      operation
      variables
      success
      errors
      timestamp
      actor
    }
  }
`;

// Active app count bound to a cluster (#393). Counts both
// ``RegisteredApp.default_tenant_cluster`` and per-env
// ``AppEnvironment.tenant_cluster`` bindings.
export const CLUSTER_APP_COUNT = gql`
  query ClusterAppCount($clusterId: GUID!) {
    astroliftAppCountForCluster(clusterId: $clusterId)
  }
`;

// Driver-recipe bootstrap plan (#67). Static read — the recipe lives in
// the driver code, so this resolver does no cluster API calls. UI
// renders the components as an interactive checklist and feeds the
// operator's selections to ``installClusterPrereqs``.
export const CLUSTER_BOOTSTRAP_PLAN = gql`
  query ClusterBootstrapPlan($clusterId: GUID!) {
    astroliftClusterBootstrapPlan(clusterId: $clusterId) {
      clusterId
      providerPluginSlug
      components {
        key
        title
        defaultEnabled
        installedByRecipe
        runningOutsideRecipe
        rationale
        helmValues
        requires
        options {
          key
          label
          default
          choices {
            value
            label
          }
        }
      }
    }
  }
`;

// Fires InstallClusterPrereqsWorkflow with the operator's selection
// (#66). Returns the cluster row; FE polls cluster lifecycle while
// the workflow runs. Idempotent: re-running with a different
// selection converges via Flux reconcile.
export const INSTALL_CLUSTER_PREREQS = gql`
  mutation InstallClusterPrereqs($input: InstallClusterPrereqsInputType!) {
    installClusterPrereqs(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        lifecycle
        lastManagementError
      }
    }
  }
`;

// Deploy the in-cluster keep-alive agent (#873). Applies the agent's
// Namespace + Deployment via the cluster driver; the agent reads its
// credentials from the astrolift-agent Secret the operator created from
// the issueClusterAgentKey snippet. Returns the cluster so the card's
// agentProvisioned + heartbeatStatus stay consistent.
export const DEPLOY_CLUSTER_AGENT = gql`
  mutation DeployClusterAgent($input: DeployClusterAgentInput!) {
    deployClusterAgent(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        agentProvisioned
        heartbeatStatus
      }
    }
  }
`;

export const LIST_PROVIDER_PLUGINS = gql`
  query ListProviderPlugins {
    astroliftProviderPlugins {
      id
      slug
      name
      version
      capabilitiesManifest
      isEnabled
    }
  }
`;

// Driver-sourced region list for the cluster-register dialog's region
// picker (#860). Keyed by the selected provider plugin slug — AWS goes
// live via ec2:DescribeRegions (static fallback), GCP/Azure return
// curated static lists, k8s_native returns []. The dialog layers
// free-text entry on top so an empty/errored list degrades to the old
// free-entry behavior.
export const PROVIDER_REGIONS = gql`
  query ProviderRegions($providerPluginSlug: String!) {
    astroliftProviderRegions(providerPluginSlug: $providerPluginSlug) {
      id
      label
      continent
    }
  }
`;

// Cognito user pools reachable in a cluster's region, for the ingress
// auth-gate picker (#859). Replaces the free-text pool-ARN / domain
// inputs; selecting a pool auto-fills the domain. AWS-only; non-AWS
// clusters return []. Degrades to free-entry on driver/credential
// failure.
export const COGNITO_USER_POOLS = gql`
  query CognitoUserPools($clusterId: GUID!) {
    astroliftCognitoUserPools(clusterId: $clusterId) {
      poolId
      poolArn
      name
      domain
      region
    }
  }
`;

// App clients within a selected Cognito user pool (#859). Populates the
// dependent client picker once a pool is chosen.
export const COGNITO_USER_POOL_CLIENTS = gql`
  query CognitoUserPoolClients($clusterId: GUID!, $poolId: String!) {
    astroliftCognitoUserPoolClients(clusterId: $clusterId, poolId: $poolId) {
      clientId
      clientName
    }
  }
`;

// Per-Deployment workload health rollup for the Status tab (#362).
// Drives the Workload health card — desired vs ready replicas, 24h
// restart counts, last completed rollout timestamp.
export const CLUSTER_WORKLOAD_HEALTH = gql`
  query ClusterWorkloadHealth($clusterId: GUID!) {
    astroliftClusterWorkloadHealth(clusterId: $clusterId) {
      namespace
      workloadName
      desiredReplicas
      readyReplicas
      restartCount24h
      lastImageDeployedAt
    }
  }
`;

// Prometheus-sourced cluster saturation metrics for the Status tab
// Metrics card (#771). Instant queries — node count, pod running
// ratio, CPU/memory utilization, deployment health ratio.
export const CLUSTER_PROMETHEUS_METRICS = gql`
  query ClusterPrometheusMetrics($clusterId: GUID!) {
    astroliftClusterPrometheusMetrics(clusterId: $clusterId) {
      available
      reason
      nodeCount
      podRunningRatio
      cpuUtilization
      memoryUtilization
      deploymentReadyRatio
    }
  }
`;

// Prometheus range-query (historical) metrics for the Status tab sparkline
// charts (#772). Returns one series per golden signal with dense point arrays.
// rangeSeconds: 3600 (1h) | 21600 (6h) | 86400 (24h)
// stepSeconds: auto-scaled to ~60-96 points per window.
export const CLUSTER_PROMETHEUS_RANGE_METRICS = gql`
  query ClusterPrometheusRangeMetrics($clusterId: GUID!, $rangeSeconds: Int, $stepSeconds: Int) {
    astroliftClusterPrometheusRangeMetrics(
      clusterId: $clusterId
      rangeSeconds: $rangeSeconds
      stepSeconds: $stepSeconds
    ) {
      available
      reason
      rangeSeconds
      stepSeconds
      series {
        metric
        label
        unit
        current
        points {
          ts
          value
        }
      }
    }
  }
`;

// Cloud-provider (CloudWatch ALB) system/ingress metrics for the
// platform metrics dashboard's System metrics panel. AWS-only — non-AWS
// providers return available=false / reason='not_supported'. Sources
// request rate, error rate, and p95 latency from CloudWatch with no
// in-app instrumentation. Omit appNamespace and the backend picks the
// cluster's primary app namespace (falls back to astrolift-system),
// echoing the resolved scope back.
export const CLUSTER_SYSTEM_METRICS = gql`
  query ClusterSystemMetrics(
    $clusterId: GUID!
    $appNamespace: String
    $rangeSeconds: Int
    $stepSeconds: Int
  ) {
    astroliftClusterSystemMetrics(
      clusterId: $clusterId
      appNamespace: $appNamespace
      rangeSeconds: $rangeSeconds
      stepSeconds: $stepSeconds
    ) {
      available
      reason
      source
      appNamespace
      rangeSeconds
      stepSeconds
      series {
        metric
        label
        unit
        current
        points {
          ts
          value
        }
      }
    }
  }
`;

// Certificate picker for the SNI / custom-domain field (#858). Lists
// the cluster provider's TLS certs (AWS → ACM via the EKS driver) so
// the operator selects instead of pasting an ARN. `supported` is false
// for providers without cert listing wired (GCP / Azure / k8s_native)
// — the UI falls back to a free-text ARN field.
export const CLUSTER_CERTIFICATES = gql`
  query ClusterCertificates($clusterId: GUID!) {
    astroliftClusterCertificates(clusterId: $clusterId) {
      supported
      certificates {
        arn
        name
        domainName
        status
      }
    }
  }
`;

// DNS hosted-zone picker for the "Add managed domain" dialog (#861).
// Keyed by the DNS-driver slug (no cluster context at dialog time);
// selecting a zone auto-fills the dialog's config textarea from the
// zone's pre-serialized configJson. `supported` is false for drivers
// without zone discovery wired (cloud_dns / azure_dns today).
export const DNS_ZONES = gql`
  query DnsZones($dnsDriver: String!) {
    astroliftDnsZones(dnsDriver: $dnsDriver) {
      supported
      zones {
        id
        name
        private
        configJson
      }
    }
  }
`;

// Cert picker for the managed-domain dialog's certificate_arn key
// (#858). Driver-keyed analog of CLUSTER_CERTIFICATES for the dialog,
// which has no cluster context; for route53 the certs come from the
// region-scoped ACM client.
export const DNS_CERTIFICATES = gql`
  query DnsCertificates($dnsDriver: String!) {
    astroliftDnsCertificates(dnsDriver: $dnsDriver) {
      supported
      certificates {
        arn
        name
        domainName
        status
      }
    }
  }
`;

// The users of a cluster's central auth (#2131). Passwords go in and never
// come back: no selection below can carry one.
export const CLUSTER_AUTH_USERS = gql`
  query ClusterAuthUsers($clusterId: GUID!, $search: String) {
    astroliftClusterAuthUsers(clusterId: $clusterId, search: $search) {
      supported
      reason
      provider
      reachNote
      groups
      users {
        username
        email
        enabled
        status
        createdAt
        groups
      }
    }
  }
`;

export const CREATE_CLUSTER_AUTH_USER = gql`
  mutation CreateClusterAuthUser($input: CreateClusterAuthUserInput!) {
    createClusterAuthUser(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        username
        email
      }
    }
  }
`;

export const SET_CLUSTER_AUTH_USER_PASSWORD = gql`
  mutation SetClusterAuthUserPassword($input: SetClusterAuthUserPasswordInput!) {
    setClusterAuthUserPassword(input: $input) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const RESET_CLUSTER_AUTH_USER_PASSWORD = gql`
  mutation ResetClusterAuthUserPassword($input: ClusterAuthUserRefInput!) {
    resetClusterAuthUserPassword(input: $input) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const SET_CLUSTER_AUTH_USER_ENABLED = gql`
  mutation SetClusterAuthUserEnabled($input: SetClusterAuthUserEnabledInput!) {
    setClusterAuthUserEnabled(input: $input) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const DELETE_CLUSTER_AUTH_USER = gql`
  mutation DeleteClusterAuthUser($input: ClusterAuthUserRefInput!) {
    deleteClusterAuthUser(input: $input) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const SET_CLUSTER_AUTH_USER_GROUPS = gql`
  mutation SetClusterAuthUserGroups($input: SetClusterAuthUserGroupsInput!) {
    setClusterAuthUserGroups(input: $input) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const CREATE_CLUSTER_AUTH_GROUP = gql`
  mutation CreateClusterAuthGroup($input: CreateClusterAuthGroupInput!) {
    createClusterAuthGroup(input: $input) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const GET_PROVIDER_PLUGIN_REFERENCE = gql`
  query GetProviderPluginReference($slug: String!, $expectedId: GUID) {
    astroliftProviderPlugin(slug: $slug, expectedId: $expectedId) {
      id
      slug
      name
      version
      capabilitiesManifest
      isEnabled
    }
  }
`;
