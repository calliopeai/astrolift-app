import { gql } from "@apollo/client";

export const DEPLOYMENT_LIFECYCLE_STREAM = gql`
  subscription DeploymentLifecycleStream($appSlug: String) {
    astroliftDeploymentLifecycleStream(appSlug: $appSlug) {
      deploymentId
      registeredAppSlug
      environmentName
      status
      occurredAt
    }
  }
`;

export const ON_APP_LOG = gql`
  subscription OnAppLog(
    $appSlug: String!
    $podName: String!
    $workloadSlug: String
    $container: String
    $follow: Boolean
    $tailLines: Int
  ) {
    astroliftOnAppLog(
      appSlug: $appSlug
      podName: $podName
      workloadSlug: $workloadSlug
      container: $container
      follow: $follow
      tailLines: $tailLines
    ) {
      podName
      container
      timestamp
      message
      stream
    }
  }
`;
