import { gql } from "@apollo/client";

export const MODEL_CONNECTION_POLICY_FIELDS = gql`
  fragment ModelConnectionPolicyFields on ModelConnectionPolicy {
    id
    version
    mode
    requiredApprovals
    allowSelfApproval
  }
`;
export const MODEL_CONNECTION_REQUEST_FIELDS = gql`
  fragment ModelConnectionRequestFields on ModelConnectionRequest {
    id
    version
    status
    organizationId
    modelDeploymentId
    appId
    appEnvironmentId
    clusterId
    providerId
    alias
    modelName
    appName
    environmentName
    requesterUsername
    policyVersion
    requiredApprovals
    approvalCount
    canApprove
    canReject
    canCancel
    canFinalize
    subscriptionId
    createdAt
    decidedAt
    finalizedAt
  }
`;
export const GET_MODEL_CONNECTION_CAPABILITIES = gql`
  query GetModelConnectionCapabilities {
    astroliftServerInfo {
      capabilities
    }
  }
`;
export const LIST_MODEL_CONNECTION_TARGETS = gql`
  query ListModelConnectionTargets(
    $input: ModelConnectionPlacementInput!
    $search: String
    $page: Int!
    $pageSize: Int!
  ) {
    modelConnectionTargetsPage(input: $input, search: $search, page: $page, pageSize: $pageSize) {
      totalCount
      page
      pageSize
      nextCursor
      items {
        environmentId
        environmentVersion
        clusterId
        appSlug
        environmentName
        eligible
        reason
        appVersion
        action
        policyVersion
        requiredApprovals
        allowSelfApproval
      }
    }
  }
`;
export const GET_MODEL_CONNECTION_ACTION = gql`
  query GetModelConnectionAction($input: ModelConnectionTargetInput!) {
    modelConnectionAction(input: $input) {
      action
      reason
      policyVersion
      requiredApprovals
      allowSelfApproval
    }
  }
`;
export const GET_ORGANIZATION_MODEL_CONNECTION_POLICY = gql`
  query GetOrganizationModelConnectionPolicy($organizationId: GUID!) {
    organizationModelConnectionPolicy(organizationId: $organizationId) {
      ...ModelConnectionPolicyFields
    }
  }
  ${MODEL_CONNECTION_POLICY_FIELDS}
`;
export const GET_MODEL_CONNECTION_RESTRICTION = gql`
  query GetModelConnectionRestriction($input: ModelConnectionPlacementInput!) {
    modelConnectionRestriction(input: $input) {
      ...ModelConnectionPolicyFields
    }
  }
  ${MODEL_CONNECTION_POLICY_FIELDS}
`;
export const LIST_MODEL_CONNECTION_REQUESTS = gql`
  query ListModelConnectionRequests(
    $organizationId: GUID!
    $page: Int!
    $pageSize: Int!
    $review: Boolean!
  ) {
    own: modelConnectionRequestsPage(
      organizationId: $organizationId
      page: $page
      pageSize: $pageSize
    ) @skip(if: $review) {
      totalCount
      page
      pageSize
      nextCursor
      items {
        ...ModelConnectionRequestFields
      }
    }
    inbox: modelConnectionApprovalRequestsPage(
      organizationId: $organizationId
      page: $page
      pageSize: $pageSize
    ) @include(if: $review) {
      totalCount
      page
      pageSize
      nextCursor
      items {
        ...ModelConnectionRequestFields
      }
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
export const GET_MODEL_CONNECTION_REQUEST = gql`
  query GetModelConnectionRequest($input: DecideModelConnectionRequestInput!, $review: Boolean!) {
    own: modelConnectionRequest(input: $input) @skip(if: $review) {
      ...ModelConnectionRequestFields
    }
    inbox: modelConnectionReviewRequest(input: $input) @include(if: $review) {
      ...ModelConnectionRequestFields
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
