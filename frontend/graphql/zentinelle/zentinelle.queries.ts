import { gql } from "@apollo/client";

// The Zentinelle integration screen (/secure/zentinelle, #1888). Every read
// is gated on its own zentinelle.* permission and scoped server-side.

export const ZENTINELLE_CLUSTER_FIELDS = gql`
  fragment ZentinelleClusterFields on AstroliftZentinelleClusterGateway {
    clusterId
    clusterSlug
    zentinelleClusterId
    gatewayName
    gatewayEnabled
    gatewayDeployed
    status
    registeredAt
    credentialRotatedAt
    lastError
    unregistered
    gatewayVersion
    lastSeenAt
  }
`;

export const ZENTINELLE_CONNECTION_FIELDS = gql`
  fragment ZentinelleConnectionFields on AstroliftZentinelleConnection {
    id
    baseUrl
    gatewayUrl
    status
    zentinelleInstallId
    tenantIds
    connectedAt
    disconnectedAt
    lastError
    gatewayFeatureEnabled
    serverVersion
    clusters {
      ...ZentinelleClusterFields
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;

/** Whether the organization is connected; any member may ask. */
export const ZENTINELLE_CAPABILITY = gql`
  query ZentinelleCapability {
    astroliftZentinelleCapability {
      connected
    }
  }
`;

/** The organization's connection and clusters (zentinelle.status_view). */
export const ZENTINELLE_CONNECTION = gql`
  query ZentinelleConnection {
    astroliftZentinelleConnection {
      ...ZentinelleConnectionFields
    }
  }
  ${ZENTINELLE_CONNECTION_FIELDS}
`;

/** The install connection shared clusters register through (#1905). Platform
 * operator only: anyone else gets an error, which is how the screen tells. */
export const ZENTINELLE_INSTALL_CONNECTION = gql`
  query ZentinelleInstallConnection {
    astroliftZentinelleInstallConnection {
      ...ZentinelleConnectionFields
    }
  }
  ${ZENTINELLE_CONNECTION_FIELDS}
`;

/** The health card's counts from Zentinelle (zentinelle.status_view, #2436). */
export const ZENTINELLE_HEALTH = gql`
  query ZentinelleHealth {
    astroliftZentinelleHealth {
      available
      windowHours
      blocks
      openApprovals
    }
  }
`;

export const ZENTINELLE_USAGE = gql`
  query ZentinelleUsage($since: DateTime, $agentSlug: String, $projectId: GUID, $teamId: GUID) {
    astroliftZentinelleUsage(
      since: $since
      agentSlug: $agentSlug
      projectId: $projectId
      teamId: $teamId
    ) {
      since
      rows {
        agentSlug
        provider
        model
        requests
        inputTokens
        outputTokens
      }
    }
  }
`;

export const ZENTINELLE_POLICIES = gql`
  query ZentinellePolicies($agentSlug: String, $projectId: GUID) {
    astroliftZentinellePolicies(agentSlug: $agentSlug, projectId: $projectId) {
      id
      name
      policyType
      enforcement
      scope
      priority
      description
    }
  }
`;

export const ZENTINELLE_AUDIT_PAGE = gql`
  query ZentinelleAuditPage(
    $since: DateTime
    $cursor: String
    $limit: Int!
    $agentSlug: String
    $projectId: GUID
    $teamId: GUID
  ) {
    astroliftZentinelleAuditPage(
      since: $since
      cursor: $cursor
      limit: $limit
      agentSlug: $agentSlug
      projectId: $projectId
      teamId: $teamId
    ) {
      events {
        id
        occurredAt
        action
        resourceType
        resourceId
        resourceName
        agentSlug
        agentId
        actor
      }
      nextCursor
    }
  }
`;

/** Projects and teams to narrow usage, policies and audit by. */
export const ZENTINELLE_SCOPE_OPTIONS = gql`
  query ZentinelleScopeOptions {
    astroliftProjectsPage(limit: 100) {
      items {
        id
        name
      }
    }
    astroliftTeamsPage(limit: 100) {
      items {
        id
        name
      }
    }
  }
`;

/** Clusters matching a search, to register with Zentinelle. */
export const ZENTINELLE_CLUSTER_CHOICES = gql`
  query ZentinelleClusterChoices($search: String) {
    astroliftClustersPage(search: $search, limit: 10) {
      items {
        id
        slug
        name
        organizationSlug
      }
    }
  }
`;
