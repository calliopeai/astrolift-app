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
  query ClusterBootstrapRuns($limit: Int) {
    astroliftClusters {
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
      cpuUtilization
      memoryUtilization
      podTotal
      podsByNamespace
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
