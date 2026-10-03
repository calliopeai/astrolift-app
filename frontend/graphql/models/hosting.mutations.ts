import { gql } from "@apollo/client";
import { MODEL_MUTATION_ERROR_FIELDS } from "./shared-models.mutations";

export const CONNECT_HUGGING_FACE = gql`
  mutation ConnectHuggingFace($input: ConnectHuggingFaceInput!) {
    connectHuggingFace(input: $input) {
      ok
      data {
        id
        version
        name
        accountUsername
        verifiedAt
      }
      errors {
        ...ModelMutationErrorFields
      }
    }
  }
  ${MODEL_MUTATION_ERROR_FIELDS}
`;
