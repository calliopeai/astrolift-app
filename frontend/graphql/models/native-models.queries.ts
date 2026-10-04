import { gql } from "@apollo/client";

export const NATIVE_MODEL_SOURCE_FIELDS = gql`
  fragment NativeModelSourceFields on NativeModelConnectionSource {
    protocol
    sourceKind
    accountId
    region
    partition
    sourceId
    sourceArn
    destinationModelArns
    sourceFingerprint
    metadataObservedAt
    configurationState
    invokeAccess
  }
`;
export const BEDROCK_MODEL_SOURCE_FIELDS = gql`
  fragment BedrockModelSourceFields on BedrockModelSource {
    identity {
      ...NativeModelSourceFields
    }
    name
    provider
    inputModalities
    outputModalities
    streaming
    lifecycle
    profileType
    registerable
    reason
  }
  ${NATIVE_MODEL_SOURCE_FIELDS}
`;
export const GET_BEDROCK_MODEL_SUPPORT = gql`
  query GetBedrockModelSupport($organizationId: GUID!) {
    bedrockModelConnectionSupport(organizationId: $organizationId) {
      enabled
      allowed
      reason
    }
  }
`;
export const GET_BEDROCK_MODEL_ACTION = gql`
  query GetBedrockModelAction($input: BedrockModelPlacementInput!) {
    bedrockModelConnectionAction(input: $input) {
      enabled
      allowed
      reason
    }
  }
`;
export const LIST_NATIVE_MODEL_CLUSTERS = gql`
  query ListNativeModelClusters(
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
        version
        providerId
        providerVersion
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
export const LIST_BEDROCK_MODEL_SOURCES = gql`
  query ListBedrockModelSources(
    $input: BedrockModelPlacementInput!
    $sourceKind: BedrockModelSourceKind!
    $limit: Int!
  ) {
    bedrockModelSources(input: $input, sourceKind: $sourceKind, limit: $limit) {
      items {
        ...BedrockModelSourceFields
      }
      state
      reason
      truncated
      partial
    }
  }
  ${BEDROCK_MODEL_SOURCE_FIELDS}
`;
export const GET_BEDROCK_MODEL_SOURCE = gql`
  query GetBedrockModelSource($input: BedrockModelSourceInput!) {
    bedrockModelSource(input: $input) {
      ...BedrockModelSourceFields
    }
  }
  ${BEDROCK_MODEL_SOURCE_FIELDS}
`;
