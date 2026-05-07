import { gql } from "@apollo/client";

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
