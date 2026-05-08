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
