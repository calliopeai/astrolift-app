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
