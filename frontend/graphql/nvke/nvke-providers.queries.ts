import { gql } from "@apollo/client";

export const GET_NVKE_PROVIDER_OBSERVATION = gql`
  query GetNvkeProviderObservation(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
  ) {
    astroliftNvkeProviderObservation(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
    ) {
      ok
      errors {
        code
      }
      data {
        siteId
        operationId
        operationVersion
        sourceSiteVersion
        connectionId
        connectionVersion
        upstreamRevision
        observedAt
        complete
        providers {
          provider
          state
          reason
          complete
          crdsEstablished
          controllerAvailable
          managerNamespace
          managerName
          readyReplicas
          desiredReplicas
          secretNamespace
          secretName
          secretPresent
          endpointConfigured
          unconfiguredIsValid
          credentials {
            health
            reason
            identityNamespace
            identityName
            identityGeneration
            readyStatus
            observedGeneration
            lastCheckedTime
            nextCheckTime
            additionalIdentityCount
          }
        }
      }
    }
  }
`;

export const GET_NVKE_PROVIDER_IDENTITIES = gql`
  query GetNvkeProviderIdentities(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
    $provider: String!
    $search: String
    $limit: Int = 25
    $after: String
  ) {
    astroliftNvkeProviderIdentitiesPage(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
      provider: $provider
      search: $search
      limit: $limit
      after: $after
    ) {
      ok
      errors {
        code
      }
      data {
        siteId
        operationId
        operationVersion
        observedAt
        provider
        totalCount
        nextCursor
        items {
          key
          namespace
          name
        }
      }
    }
  }
`;
