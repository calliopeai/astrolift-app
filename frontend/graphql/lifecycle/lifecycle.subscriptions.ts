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
    $previewId: GUID
    $expectedEnvironmentId: GUID
    $ifMatchPreviewVersion: Int
    $ifMatchEnvironmentVersion: Int
  ) {
    astroliftOnAppLog(
      appSlug: $appSlug
      podName: $podName
      workloadSlug: $workloadSlug
      container: $container
      follow: $follow
      tailLines: $tailLines
      previewId: $previewId
      expectedEnvironmentId: $expectedEnvironmentId
      ifMatchPreviewVersion: $ifMatchPreviewVersion
      ifMatchEnvironmentVersion: $ifMatchEnvironmentVersion
    ) {
      podName
      container
      timestamp
      message
      stream
    }
  }
`;

// #482 — multi-pod aggregated live stream. Used when the operator
// selects "All replicas" instead of a specific pod. Reuses the
// AstroliftAppLogLine shape (the per-line pod_name tag is what lets
// the FE colorize / group per replica).
export const ON_APP_LOGS = gql`
  subscription OnAppLogs(
    $appSlug: String!
    $environmentName: String
    $workloadSlug: String
    $container: String
    $follow: Boolean
    $tailLines: Int
    $previewId: GUID
    $expectedEnvironmentId: GUID
    $ifMatchPreviewVersion: Int
    $ifMatchEnvironmentVersion: Int
  ) {
    astroliftOnAppLogs(
      appSlug: $appSlug
      environmentName: $environmentName
      workloadSlug: $workloadSlug
      container: $container
      follow: $follow
      tailLines: $tailLines
      previewId: $previewId
      expectedEnvironmentId: $expectedEnvironmentId
      ifMatchPreviewVersion: $ifMatchPreviewVersion
      ifMatchEnvironmentVersion: $ifMatchEnvironmentVersion
    ) {
      podName
      container
      timestamp
      message
      stream
    }
  }
`;
