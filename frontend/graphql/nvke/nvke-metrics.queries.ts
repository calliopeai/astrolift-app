import { gql } from "@apollo/client";

export const NVKE_METRICS_AUTHORITY_FIELDS = gql`
  fragment NvkeMetricsAuthorityFields on NvkeMetricsAuthorityType {
    id
    version
    siteId
    siteVersion
    endpoint
    allowHttp
    isEnabled
    timeoutSeconds
    hasCredentials
    verifiedAt
    verificationCode
    deletedAt
  }
`;

export const GET_NVKE_METRICS_AUTHORITY = gql`
  query GetNvkeMetricsAuthority($siteId: GUID!) {
    astroliftNvkeMetricsAuthority(siteId: $siteId) {
      ...NvkeMetricsAuthorityFields
    }
  }
  ${NVKE_METRICS_AUTHORITY_FIELDS}
`;

export const GET_NVKE_CREDENTIAL_OBSERVATION = gql`
  query GetNvkeCredentialObservation(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
  ) {
    astroliftNvkeCredentialObservation(
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
        clusterId
        sourceOperationId
        sourceVersion
        authorityId
        authorityVersion
        scrapedAt
        clusterReported
        collectionSucceeded
        monitoringEnabled
        renewalAutomationEnabled
        warningWindowSeconds
      }
    }
  }
`;

export const GET_NVKE_CREDENTIAL_METRICS = gql`
  query GetNvkeCredentialMetrics(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
    $after: String
    $limit: Int = 25
    $search: String
  ) {
    astroliftNvkeCredentialMetricsPage(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
      after: $after
      limit: $limit
      search: $search
    ) {
      ok
      errors {
        code
      }
      data {
        siteId
        operationId
        operationVersion
        scrapedAt
        totalCount
        nextCursor
        items {
          key
          metric
          node
          credential
          state
          value
        }
      }
    }
  }
`;

export const GET_NVKE_CREDENTIAL_CERTIFICATES = gql`
  query GetNvkeCredentialCertificates(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
    $after: String
    $limit: Int = 25
    $search: String
  ) {
    astroliftNvkeCredentialCertificatesPage(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
      after: $after
      limit: $limit
      search: $search
    ) {
      ok
      errors {
        code
      }
      data {
        siteId
        operationId
        operationVersion
        scrapedAt
        totalCount
        nextCursor
        items {
          key
          node
          credential
          required
          observationAvailable
          latestInspectionAvailable
          observationAgeSeconds
          reportedNotAfter
          reportedExpiredAtScrape
        }
      }
    }
  }
`;
