import { gql } from "@apollo/client";

export const CLUSTER_MODEL_FIELDS = gql`
  fragment ClusterModelFields on ClusterModelDeployment {
    id
    version
    name
    organizationId
    clusterId
    providerId
    clusterSlug
    clusterName
    modelRepo
    sourceKind
    localArtifactId
    localArtifactVersion
    localManifestSha256
    revisionSha
    computeMode
    subscriptionsEnabled
    sharingMode
    dedicatedAppId
    dedicatedAppVersion
    dedicatedAppName
    dedicatedAppSlug
    runtimeSupported
    runtimeReason
    status
    reason
    ready
    readinessObservedAt
    readinessGeneration
    desiredSubscriptionRevision
    appliedSubscriptionRevision
    operationId
    operationStartedAt
    operationCompletedAt
    desiredResources {
      cpuRequest
      memoryRequest
      gpuCount
      replicas
      cpuKvCacheGiB
      dtype
      maxModelLen
      maxNumSeqs
    }
    appliedResources {
      cpuRequest
      memoryRequest
      gpuCount
      replicas
      cpuKvCacheGiB
      dtype
      maxModelLen
      maxNumSeqs
    }
  }
`;

export const LIST_CLUSTER_MODELS_PAGE = gql`
  query ListClusterModelsPage(
    $organizationId: GUID!
    $search: String
    $page: Int!
    $pageSize: Int!
    $filter: ClusterModelsFilterInput
  ) {
    clusterModelDeploymentsPage(
      organizationId: $organizationId
      search: $search
      page: $page
      pageSize: $pageSize
      filter: $filter
    ) {
      items {
        ...ClusterModelFields
      }
      totalCount
      nextCursor
      page
      pageSize
    }
  }
  ${CLUSTER_MODEL_FIELDS}
`;
export const GET_CLUSTER_MODEL_DEPLOYMENT = gql`
  query GetClusterModelDeployment($organizationId: GUID!, $id: GUID!) {
    clusterModelDeployment(organizationId: $organizationId, id: $id) {
      ...ClusterModelFields
    }
  }
  ${CLUSTER_MODEL_FIELDS}
`;
export const LIST_MODEL_PLACEMENT_CLUSTERS = gql`
  query ListModelPlacementClusters(
    $organizationId: GUID!
    $search: String
    $page: Int!
    $pageSize: Int!
  ) {
    clusterModelPlacementClustersPage(
      organizationId: $organizationId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        providerId
        name
        slug
        region
      }
      totalCount
      nextCursor
      page
      pageSize
    }
  }
`;
export const GET_CLUSTER_MODEL_RUNTIME_ADMISSION = gql`
  query GetClusterModelRuntimeAdmission($input: ProvisionClusterModelInput!) {
    clusterModelRuntimeAdmission(input: $input) {
      eligible
      reason
      runtimeVersion
      architecture
      hardwareAdmission
    }
  }
`;
export const LIST_MODEL_SUBSCRIPTION_TARGETS = gql`
  query ListModelSubscriptionTargets(
    $organizationId: GUID!
    $modelDeploymentId: GUID!
    $search: String
    $page: Int!
    $pageSize: Int!
  ) {
    clusterModelSubscriptionTargetsPage(
      organizationId: $organizationId
      modelDeploymentId: $modelDeploymentId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      items {
        environmentId
        environmentVersion
        appId
        appSlug
        appName
        environmentName
        clusterId
        eligible
        reason
      }
      totalCount
      nextCursor
      page
      pageSize
    }
  }
`;
export const LIST_CLUSTER_MODEL_SUBSCRIPTIONS = gql`
  query ListClusterModelSubscriptions(
    $organizationId: GUID!
    $modelDeploymentId: GUID!
    $appEnvironmentId: GUID
    $search: String
    $page: Int!
    $pageSize: Int!
  ) {
    clusterModelSubscriptionsPage(
      organizationId: $organizationId
      modelDeploymentId: $modelDeploymentId
      appEnvironmentId: $appEnvironmentId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        version
        modelDeploymentId
        appId
        appSlug
        appName
        environmentId
        environmentName
        alias
        bindingPrefix
        status
        canRevoke
        desiredEnabled
        desiredRevision
        appliedRevision
        reason
        reconcileStartedAt
        reconciledAt
      }
      totalCount
      nextCursor
      page
      pageSize
    }
  }
`;

export const GET_CLUSTER_MODEL_UPDATE_ADMISSION = gql`
  query GetClusterModelUpdateAdmission($input: UpdateClusterModelInput!) {
    clusterModelUpdateAdmission(input: $input) {
      eligible
      reason
      runtimeVersion
      architecture
      hardwareAdmission
    }
  }
`;
export const LIST_MODEL_DEDICATED_APPS = gql`
  query ListModelDedicatedApps(
    $organizationId: GUID!
    $clusterId: GUID!
    $expectedProviderId: GUID!
    $search: String
    $page: Int!
    $pageSize: Int!
  ) {
    clusterModelDedicatedAppsPage(
      organizationId: $organizationId
      clusterId: $clusterId
      expectedProviderId: $expectedProviderId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        version
        name
        slug
      }
      totalCount
      nextCursor
      page
      pageSize
    }
  }
`;
