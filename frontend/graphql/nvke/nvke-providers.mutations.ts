import { gql } from "@apollo/client";
import { NVKE_OPERATION_FIELDS } from "./nvke.queries";
export const REQUEST_NVKE_PROVIDER_OBSERVATION = gql`
  mutation RequestNvkeProviderObservation($input: RequestNvkeProviderObservationInput!) {
    requestNvkeProviderObservation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;
