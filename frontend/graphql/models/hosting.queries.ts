import { gql } from "@apollo/client";

export const GET_MODEL_HOSTING_ACTION = gql`
  query GetModelHostingAction($organizationId: GUID!) {
    modelHostingAction(organizationId: $organizationId) {
      allowed
      reason
    }
  }
`;
export const LIST_HUGGING_FACE_CONNECTIONS = gql`
  query ListHuggingFaceConnections($organizationId: GUID!, $page: Int!, $pageSize: Int!) {
    huggingFaceConnectionsPage(organizationId: $organizationId, page: $page, pageSize: $pageSize) {
      items {
        id
        version
        name
        accountUsername
        verifiedAt
      }
      page
      pageSize
      totalCount
    }
  }
`;
export const GET_MODEL_SOURCE_ACCESS = gql`
  query GetModelSourceAccess(
    $organizationId: GUID!
    $modelRepo: String!
    $revisionSha: String!
    $connectionId: GUID
    $expectedConnectionVersion: Int
  ) {
    clusterModelSourceAccess(
      organizationId: $organizationId
      modelRepo: $modelRepo
      revisionSha: $revisionSha
      connectionId: $connectionId
      expectedConnectionVersion: $expectedConnectionVersion
    ) {
      accessible
      reason
      observedAt
      model {
        repoId
        revisionSha
        license
        gated
        architectures
        pipelineTag
        library
        author
        downloads
        likes
        compatibility
      }
    }
  }
`;
