import { gql } from "@apollo/client";

/**
 * Set (create or overwrite) a pipeline-scoped secret value.
 *
 * The value is write-only: once set it cannot be retrieved via the API.
 * Requires ``pipeline.secret.write`` permission (#86).
 *
 * Part of #100 — pipeline secret management UI.
 */
export const SET_PIPELINE_SECRET = gql`
  mutation SetPipelineSecret($input: SetPipelineSecretInput!) {
    setPipelineSecret(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        pipelineId
        name
      }
    }
  }
`;

/**
 * Delete a pipeline-scoped secret.
 *
 * Requires ``pipeline.secret.write`` permission (#86).
 * Part of #100 — pipeline secret management UI.
 */
export const DELETE_PIPELINE_SECRET = gql`
  mutation DeletePipelineSecret($input: DeletePipelineSecretInput!) {
    deletePipelineSecret(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        pipelineId
        name
      }
    }
  }
`;
