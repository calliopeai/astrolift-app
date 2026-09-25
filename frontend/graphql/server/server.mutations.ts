import { gql } from "@apollo/client";

// Platform-admin runtime feature flipper. Sets the Constance value behind
// a PUBLIC dotted key (from `astroliftServerInfo.featureFlags`) and returns
// the updated flag. Only the platform operator may call it (server-side, not
// `admin.elevate` alone, which org admins hold); unknown / non-public keys
// (and install-time features) are rejected.
export const SET_FEATURE_FLAG = gql`
  mutation SetFeatureFlag($key: String!, $enabled: Boolean!) {
    setFeatureFlag(key: $key, enabled: $enabled) {
      ok
      errors {
        code
        message
        field
      }
      data {
        key
        enabled
        description
      }
    }
  }
`;
