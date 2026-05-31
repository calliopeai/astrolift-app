import { gql } from "@apollo/client";

/** Fetch a single pipeline by ID, including its declared secrets list. */
export const GET_PIPELINE = gql`
  query GetPipeline($id: UUID!) {
    astroliftPipeline(id: $id) {
      id
      name
      slug
      description
      createdAt
      updatedAt
      secrets {
        id
        name
        createdAt
        updatedAt
      }
    }
  }
`;

/**
 * List all pipeline secrets for a given pipeline. Used to refetch the
 * secrets list after set/delete mutations.
 */
export const LIST_PIPELINE_SECRETS = gql`
  query ListPipelineSecrets($pipelineId: UUID!) {
    astroliftPipelineSecrets(pipelineId: $pipelineId) {
      id
      name
      createdAt
      updatedAt
    }
  }
`;
