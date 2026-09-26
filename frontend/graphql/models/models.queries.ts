import { gql } from "@apollo/client";

// Every model endpoint the caller can read, app- or project-owned (#2040).
export const LIST_MODEL_ENDPOINTS = gql`
  query ListModelEndpoints {
    astroliftModelEndpoints {
      id
      name
      variant
      status
      statusError
      config
      registeredAppSlug
      projectSlug
      ownerScope
      clusterSlug
      environmentName
    }
  }
`;

// Where a model can go: the org's app environments and their clusters' GPU
// facts for the fit check. Clusters are read best-effort, since a caller who
// can deploy to an app may not read clusters.
export const LIST_MODEL_TARGETS = gql`
  query ListModelTargets {
    astroliftEnvironments {
      id
      name
      registeredAppSlug
      clusterId
      clusterSlug
    }
  }
`;

export const LIST_CLUSTER_GPUS = gql`
  query ListClusterGpus {
    astroliftClusters {
      id
      capabilities
    }
  }
`;
