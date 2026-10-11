import { gql } from "@apollo/client";
import { MODEL_RUNTIME_SETTINGS_FIELDS } from "./model-runtime-settings.queries";
export const UPDATE_CLUSTER_MODEL_RUNTIME = gql`
  mutation UpdateClusterModelRuntime($input: UpdateClusterModelRuntimeInput!) {
    updateClusterModelRuntime(input: $input) {
      ok
      errors {
        code
        message
        currentVersion
        requestedVersion
        field
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelRuntimeSettingsFields
      }
    }
  }
  ${MODEL_RUNTIME_SETTINGS_FIELDS}
`;
