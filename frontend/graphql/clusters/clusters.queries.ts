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
      isActive
      capabilities
      capabilitiesProbedAt
      createdAt
      lifecycle
      lastManagementError
      managedAt
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
