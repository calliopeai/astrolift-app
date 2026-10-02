import { gql } from "@apollo/client";

export const MANAGED_RESOURCE_CONTEXT = gql`
  fragment ManagedResourceContext on AstroliftManagedServiceContext {
    id
    contextRevision
    version
    name
    kind
    variant
    status
    ownerScope
    organizationId
    projectId
    projectSlug
    registeredAppId
    registeredAppSlug
    clusterId
    clusterSlug
    clusterVersion
    environmentId
    environmentName
    environmentVersion
    createdAt
    updatedAt
    operationKind
    operationWorkflowId
    operationRunId
    operationStartedAt
    operationCompletedAt
  }
`;

export const LIST_PROJECT_RESOURCE_PAGE = gql`
  query ListProjectResourcePage(
    $projectId: GUID!
    $search: String
    $name: String
    $kinds: [String!]
    $statuses: [String!]
    $environmentName: String
    $clusterId: GUID
    $limit: Int = 25
    $after: String
  ) {
    astroliftProjectManagedServicesPage(
      projectId: $projectId
      search: $search
      name: $name
      kinds: $kinds
      statuses: $statuses
      environmentName: $environmentName
      clusterId: $clusterId
      limit: $limit
      after: $after
    ) {
      items {
        ...ManagedResourceContext
      }
      totalCount
      nextCursor
    }
  }
  ${MANAGED_RESOURCE_CONTEXT}
`;

export const GET_PROJECT_RESOURCE = gql`
  query GetProjectResource($projectId: GUID!, $id: GUID!, $expectedContextRevision: String) {
    astroliftProjectManagedService(
      projectId: $projectId
      id: $id
      expectedContextRevision: $expectedContextRevision
    ) {
      ...ManagedResourceContext
    }
  }
  ${MANAGED_RESOURCE_CONTEXT}
`;

export const GET_MANAGED_RESOURCE = gql`
  query GetManagedResource($id: GUID!, $expectedContextRevision: String) {
    astroliftManagedService(id: $id, expectedContextRevision: $expectedContextRevision) {
      ...ManagedResourceContext
    }
  }
  ${MANAGED_RESOURCE_CONTEXT}
`;

export const LIST_PROJECT_RESOURCE_ATTACHMENTS_PAGE = gql`
  query ListProjectResourceAttachmentsPage(
    $projectId: GUID!
    $managedServiceId: GUID!
    $expectedContextRevision: String
    $limit: Int = 25
    $after: String
  ) {
    astroliftProjectManagedServiceAttachmentsPage(
      projectId: $projectId
      managedServiceId: $managedServiceId
      expectedContextRevision: $expectedContextRevision
      limit: $limit
      after: $after
    ) {
      items {
        id
        version
        managedServiceId
        consumerKind
        consumerId
        consumerSlug
        registeredAppId
        environmentId
        environmentName
        clusterId
        createdAt
      }
      totalCount
      nextCursor
    }
  }
`;
