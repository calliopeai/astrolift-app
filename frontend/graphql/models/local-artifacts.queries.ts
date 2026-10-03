import { gql } from "@apollo/client";
import { MODEL_MUTATION_ERROR_FIELDS } from "./shared-models.mutations";
export const LOCAL_ARTIFACT_FIELDS = gql`
  fragment LocalArtifactFields on LocalModelArtifact {
    id
    name
    version
    state
    manifestSha256
    fileCount
    sizeBytes
  }
`;
export const LIST_LOCAL_MODEL_ARTIFACTS = gql`
  query ListLocalModelArtifacts($search: String, $limit: Int!, $after: String) {
    astroliftLocalModelArtifactsPage(search: $search, limit: $limit, after: $after) {
      items {
        ...LocalArtifactFields
      }
      nextCursor
    }
  }
  ${LOCAL_ARTIFACT_FIELDS}
`;
export const BEGIN_LOCAL_MODEL_ARTIFACT = gql`
  mutation BeginLocalModelArtifact($input: BeginLocalModelArtifactInput!) {
    beginLocalModelArtifact(input: $input) {
      ok
      data {
        ...LocalArtifactFields
      }
      errors {
        ...ModelMutationErrorFields
      }
    }
  }
  ${LOCAL_ARTIFACT_FIELDS}
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
export const AUTHORIZE_LOCAL_MODEL_UPLOADS = gql`
  mutation AuthorizeLocalModelUploads($input: LocalModelArtifactIdentityInput!) {
    authorizeLocalModelUploads(input: $input) {
      ok
      data {
        artifact {
          ...LocalArtifactFields
        }
        files {
          name
          sizeBytes
          uploadUrl
          headers {
            name
            value
          }
        }
        expiresInSeconds
      }
      errors {
        ...ModelMutationErrorFields
      }
    }
  }
  ${LOCAL_ARTIFACT_FIELDS}
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
export const FINALIZE_LOCAL_MODEL_ARTIFACT = gql`
  mutation FinalizeLocalModelArtifact($input: LocalModelArtifactIdentityInput!) {
    finalizeLocalModelArtifact(input: $input) {
      ok
      data {
        ...LocalArtifactFields
      }
      errors {
        ...ModelMutationErrorFields
      }
    }
  }
  ${LOCAL_ARTIFACT_FIELDS}
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
