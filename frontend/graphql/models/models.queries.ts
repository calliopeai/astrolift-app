import { gql } from "@apollo/client";

// The model endpoints the caller can read, app- or project-owned, one
// numbered page at a time (#2040, #2155). The server answers the Models
// list's views, chips, search and sort.
export const LIST_MODEL_ENDPOINTS_PAGE = gql`
  query ListModelEndpointsPage(
    $search: String
    $filter: AstroliftModelEndpointsFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftModelEndpointsPage(
      search: $search
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
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
        deployedByEmail
        deployedByMe
      }
      totalCount
      page
      pageSize
    }
  }
`;

// One model endpoint by id, inside the list's visibility; null otherwise.
export const GET_MODEL_ENDPOINT = gql`
  query GetModelEndpoint($id: GUID!) {
    astroliftModelEndpoint(id: $id) {
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
      deployedByEmail
      deployedByMe
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
