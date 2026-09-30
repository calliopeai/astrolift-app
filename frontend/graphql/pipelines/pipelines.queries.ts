import { gql } from "@apollo/client";

/** Fetch the existing pipeline definition; secret metadata has a separate gate. */
export const GET_PIPELINE = gql`
  query GetPipeline($id: String!) {
    astroliftPipeline(id: $id) {
      id
      name
      repoUrl
      defaultBranch
      tomlPath
      createdAt
      updatedAt
    }
  }
`;

/**
 * List all pipeline secrets for a given pipeline. Used to refetch the
 * secrets list after set/delete mutations.
 */
export const LIST_PIPELINE_SECRETS = gql`
  query ListPipelineSecrets($pipelineId: GUID!) {
    astroliftPipelineSecrets(pipelineId: $pipelineId) {
      id
      name
      createdAt
      updatedAt
    }
  }
`;
