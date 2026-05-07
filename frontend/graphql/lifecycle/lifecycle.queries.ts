import { gql } from "@apollo/client";

export const LIST_ENVIRONMENTS = gql`
  query ListEnvironments($appSlug: String) {
    astroliftEnvironments(appSlug: $appSlug) {
      id
      name
      url
      deploysPaused
      requiredApprovals
      registeredAppSlug
      clusterSlug
      domainZone
      createdAt
    }
  }
`;

export const LIST_DEPLOYMENTS = gql`
  query ListDeployments(
    $appSlug: String
    $environmentName: String
    $limit: Int
  ) {
    astroliftDeployments(
      appSlug: $appSlug
      environmentName: $environmentName
      limit: $limit
    ) {
      id
      registeredAppSlug
      environmentName
      workloadSlug
      triggerKind
      status
      imageTag
      imageDigest
      clusterRevision
      approvalsRequired
      approvalsReceived
      startedAt
      succeededAt
      failedAt
      endedAt
      durationSeconds
      createdAt
    }
  }
`;

export const GET_DEPLOYMENT_LOG = gql`
  query GetDeploymentLog($deploymentId: String!) {
    astroliftDeploymentLog(deploymentId: $deploymentId) {
      id
      deploymentId
      status
      message
      detail
      occurredAt
    }
  }
`;

export const LIST_PREVIEW_ENVIRONMENTS = gql`
  query ListPreviewEnvironments($appSlug: String) {
    astroliftPreviewEnvironments(appSlug: $appSlug) {
      id
      registeredAppSlug
      prNumber
      branch
      commitSha
      status
      hostname
      namespace
      lastDeployedAt
      tornDownAt
    }
  }
`;
