import { gql } from "@apollo/client";

export const NVKE_SITE_FIELDS = gql`
  fragment NvkeSiteFields on NvkeSiteType {
    id
    version
    name
    slug
    organizationNamespace
    isActive
    upstreamRevision
    createdAt
    updatedAt
    connections {
      id
      version
      surface
      endpoint
      isEnabled
      allowHttp
      timeoutSeconds
      hasCredentials
      verifiedAt
      verificationCode
    }
  }
`;

export const LIST_NVKE_SITES = gql`
  query ListNvkeSites($after: String, $limit: Int = 50) {
    astroliftNvkeSites(after: $after, limit: $limit) {
      items {
        ...NvkeSiteFields
      }
      nextCursor
      totalCount
    }
  }
  ${NVKE_SITE_FIELDS}
`;

export const GET_NVKE_SITE = gql`
  query GetNvkeSite($siteId: GUID!) {
    astroliftNvkeSite(siteId: $siteId) {
      ...NvkeSiteFields
    }
  }
  ${NVKE_SITE_FIELDS}
`;

export const NVKE_OPERATION_FIELDS = gql`
  fragment NvkeOperationFields on NvkeOperationType {
    id
    version
    siteId
    requestId
    operationKey
    status
    stage
    generation
    errorCode
    errorMessage
    cancellationRequestedAt
    startedAt
    finishedAt
    deadline
    createdAt
    updatedAt
  }
`;

export const LIST_NVKE_OPERATIONS = gql`
  query ListNvkeOperations($siteId: GUID!, $after: String, $limit: Int = 50) {
    astroliftNvkeOperations(siteId: $siteId, after: $after, limit: $limit) {
      items {
        ...NvkeOperationFields
      }
      nextCursor
      totalCount
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const GET_NVKE_OPERATION = gql`
  query GetNvkeOperation($operationId: GUID!) {
    astroliftNvkeOperation(operationId: $operationId) {
      ...NvkeOperationFields
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const GET_NVKE_OPERATION_RESULT = gql`
  query GetNvkeOperationResult($operationId: GUID!) {
    astroliftNvkeOperationResult(operationId: $operationId) {
      ok
      errors {
        code
        message
      }
      data
    }
  }
`;

export const GET_NVKE_CONTRACT = gql`
  query GetNvkeContract {
    astroliftNvkeContract {
      revision
      actions {
        key
        surface
        verb
        resourceKind
        mutates
        permission
        path
      }
      controllerOpenapi
      resources {
        kind
        namespaced
        schema
      }
    }
  }
`;

export const GET_NVKE_IDENTITY = gql`
  query GetNvkeIdentity {
    astroliftMyProfile {
      userId
    }
  }
`;

export const GET_NVKE_REQUEST = gql`
  query GetNvkeRequest($siteId: GUID!, $requestId: String!) {
    astroliftNvkeRequest(siteId: $siteId, requestId: $requestId) {
      ...NvkeOperationFields
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const GET_NVKE_OPERATION_RESULT_TEXT = gql`
  query GetNvkeOperationResultText($operationId: GUID!) {
    astroliftNvkeOperationResultText(operationId: $operationId) {
      ok
      errors {
        code
        message
      }
      data
    }
  }
`;
