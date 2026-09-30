import { gql } from "@apollo/client";

export const SEARCH_HUGGING_FACE_MODELS = gql`
  query SearchHuggingFaceModels(
    $search: String!
    $author: String!
    $pipelineTag: String!
    $library: String!
    $license: String!
    $gated: Boolean
    $sortBy: String!
    $first: Int!
    $after: String
  ) {
    astroliftHuggingFaceModels(
      search: $search
      author: $author
      pipelineTag: $pipelineTag
      library: $library
      license: $license
      gated: $gated
      sortBy: $sortBy
      first: $first
      after: $after
    ) {
      state
      source
      observedAt
      nextCursor
      retryAfterSeconds
      items {
        repoId
        revisionSha
        author
        pipelineTag
        library
        license
        gated
        architectures
        downloads
        likes
        compatibility
      }
    }
  }
`;

export const GET_HUGGING_FACE_MODEL = gql`
  query GetHuggingFaceModel($repoId: String!, $revision: String) {
    astroliftHuggingFaceModel(repoId: $repoId, revision: $revision) {
      state
      source
      observedAt
      retryAfterSeconds
      model {
        repoId
        revisionSha
        author
        pipelineTag
        library
        license
        gated
        architectures
        downloads
        likes
        compatibility
      }
    }
  }
`;
