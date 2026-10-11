import { gql } from "@apollo/client";
import { CLUSTER_MODEL_FIELDS } from "./shared-models.queries";
import { MODEL_MUTATION_ERROR_FIELDS } from "./shared-models.mutations";

export const REGISTER_BEDROCK_MODEL = gql`
  mutation RegisterBedrockModel($input: RegisterBedrockModelConnectionInput!) {
    registerBedrockModelConnection(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        ...ClusterModelFields
      }
    }
  }
  ${CLUSTER_MODEL_FIELDS}
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
export const UPDATE_BEDROCK_MODEL = gql`
  mutation UpdateBedrockModel($input: UpdateBedrockModelConnectionInput!) {
    updateBedrockModelConnection(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        ...ClusterModelFields
      }
    }
  }
  ${CLUSTER_MODEL_FIELDS}
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
export const UNREGISTER_BEDROCK_MODEL = gql`
  mutation UnregisterBedrockModel($input: UnregisterBedrockModelConnectionInput!) {
    unregisterBedrockModelConnection(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        ...ClusterModelFields
      }
    }
  }
  ${CLUSTER_MODEL_FIELDS}
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
