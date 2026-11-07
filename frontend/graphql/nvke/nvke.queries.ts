import { gql } from "@apollo/client";

export const GET_NVKE_MAINTENANCE = gql`
  query GetNvkeMaintenance(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
  ) {
    astroliftNvkeMaintenanceObservation(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
    ) {
      siteId
      operationId
      operationVersion
      observedAt
      clusterId
      nextCertificateRenewalDue
      renewalScheduleOverdueAtObservation
      mutationLockReported
      mutationLockOwner
      mutationLockStartedAt
      renewal {
        workflowId
        state
        outcome
        forced
        scheduledFor
        startedAt
        completedAt
        failedNodeId
        phase
        retainedMutationLock
      }
      etcdKeyRotation {
        workflowId
        state
        phase
        startedAt
        lastProgressAt
        completedAt
        failureReason
        retainedMutationLock
      }
    }
  }
`;

export const NVKE_ACCESS_FIELDS = gql`
  fragment NvkeAccessFields on NvkeWorkloadAccessBindingType {
    id
    version
    siteId
    controllerConnectionId
    sourceOperationId
    clusterId
    upstreamRevision
    createdAt
    deletedAt
  }
`;
export const LIST_NVKE_ACCESS = gql`
  query ListNvkeAccess($siteId: GUID!, $after: String, $limit: Int = 25) {
    astroliftNvkeWorkloadAccessBindings(siteId: $siteId, after: $after, limit: $limit) {
      items {
        ...NvkeAccessFields
      }
      nextCursor
      totalCount
    }
  }
  ${NVKE_ACCESS_FIELDS}
`;

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
    isInventory
    resourceSource {
      operationId
      version
      resourceKey
    }
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
      collections {
        key
        surface
        resourceKind
        namespaced
        permission
        defaultParametersText
        actions {
          key
          surface
          verb
          resourceKind
          mutates
          permission
          path
        }
      }
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

export const LIST_NVKE_INVENTORY_PAGE = gql`
  query ListNvkeInventoryPage(
    $operationId: GUID!
    $expectedActorId: Int!
    $after: String
    $limit: Int = 50
    $search: String = ""
  ) {
    astroliftNvkeInventoryPage(
      operationId: $operationId
      expectedActorId: $expectedActorId
      after: $after
      limit: $limit
      search: $search
    ) {
      items {
        key
        identity
        name
        uid
        namespace
        resourceVersion
        generation
        kubernetesVersion
        desiredCount
        conditions {
          name
          status
        }
        conditionsOmitted
      }
      nextCursor
      totalCount
      operationId
      observedAt
      resourceVersion
      nativePageCount
      collection {
        key
        surface
        resourceKind
        namespaced
        permission
        defaultParametersText
        actions {
          key
          surface
          verb
          resourceKind
          mutates
          permission
          path
        }
      }
    }
  }
`;

export const GET_NVKE_INVENTORY_DOCUMENT_TEXT = gql`
  query GetNvkeInventoryDocumentText(
    $operationId: GUID!
    $expectedActorId: Int!
    $resourceKey: String!
  ) {
    astroliftNvkeInventoryDocumentText(
      operationId: $operationId
      expectedActorId: $expectedActorId
      resourceKey: $resourceKey
    ) {
      ok
      errors {
        code
        message
      }
      data
    }
  }
`;

export const GET_NVKE_INVENTORY_RESOURCE = gql`
  query GetNvkeInventoryResource(
    $operationId: GUID!
    $expectedActorId: Int!
    $resourceKey: String!
  ) {
    astroliftNvkeInventoryResource(
      operationId: $operationId
      expectedActorId: $expectedActorId
      resourceKey: $resourceKey
    ) {
      operationId
      operationVersion
      siteId
      observedAt
      collection {
        key
        surface
        resourceKind
        namespaced
        permission
        defaultParametersText
        actions {
          key
          surface
          verb
          resourceKind
          mutates
          permission
          path
        }
      }
      item {
        key
        identity
        name
        uid
        namespace
        resourceVersion
        generation
        kubernetesVersion
        desiredCount
        conditions {
          name
          status
        }
        conditionsOmitted
      }
    }
  }
`;

export const GET_NVKE_RESOURCE_EDITOR = gql`
  query GetNvkeResourceEditor(
    $siteId: GUID!
    $expectedActorId: Int!
    $operationKey: String!
    $sourceOperationId: GUID!
    $expectedSourceVersion: Int!
    $sourceResourceKey: String
  ) {
    astroliftNvkeResourceEditor(
      siteId: $siteId
      expectedActorId: $expectedActorId
      operationKey: $operationKey
      sourceOperationId: $sourceOperationId
      expectedSourceVersion: $expectedSourceVersion
      sourceResourceKey: $sourceResourceKey
    ) {
      ok
      errors {
        code
        message
      }
      data {
        operationKey
        parametersText
        preparationActionKey
        concurrency
        source {
          operationId
          version
          resourceKey
        }
      }
    }
  }
`;

export const GET_NVKE_DIAGNOSTIC_SUMMARY = gql`
  query GetNvkeDiagnosticSummary(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
  ) {
    astroliftNvkeDiagnosticPage(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
      limit: 1
    ) {
      siteId
      operationId
      operationVersion
      observedAt
      summary {
        clusterId
        kind
        configurationId
        kubernetesVersion
        requestedAddress
        requestedPort
        counts {
          name
          value
        }
        flags {
          name
          value
        }
        conditions {
          name
          value
        }
      }
    }
  }
`;
export const LIST_NVKE_DIAGNOSTIC_ROWS = gql`
  query ListNvkeDiagnosticRows(
    $siteId: GUID!
    $operationId: GUID!
    $expectedActorId: Int!
    $expectedVersion: Int!
    $after: String
    $limit: Int = 25
    $search: String
  ) {
    astroliftNvkeDiagnosticPage(
      siteId: $siteId
      operationId: $operationId
      expectedActorId: $expectedActorId
      expectedVersion: $expectedVersion
      after: $after
      limit: $limit
      search: $search
    ) {
      siteId
      operationId
      operationVersion
      totalCount
      nextCursor
      items {
        key
        nativeId
        name
        createdAt
        isCurrent
        reachable
        hasValidSvid
        serverAddress
        serverPort
        svidExpiresAt
        changedFields
        changedFieldCount
        fieldsOmitted
      }
    }
  }
`;
