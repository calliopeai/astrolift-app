import { gql } from "@apollo/client";

import { PLATFORM_SETTING_FIELDS } from "./server.queries";

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

// Set or reset an install setting (#1861). Platform operator only, with a
// fresh sign-in; a setting with a `warning` needs `confirmed`.
export const SET_PLATFORM_SETTING = gql`
  mutation SetPlatformSetting($key: String!, $value: String!, $confirmed: Boolean!) {
    setPlatformSetting(key: $key, value: $value, confirmed: $confirmed) {
      ok
      errors {
        code
        message
        field
      }
      data {
        previousValue
        setting {
          ...PlatformSettingFields
        }
      }
    }
  }
  ${PLATFORM_SETTING_FIELDS}
`;

export const RESET_PLATFORM_SETTING = gql`
  mutation ResetPlatformSetting($key: String!, $confirmed: Boolean!) {
    resetPlatformSetting(key: $key, confirmed: $confirmed) {
      ok
      errors {
        code
        message
        field
      }
      data {
        previousValue
        setting {
          ...PlatformSettingFields
        }
      }
    }
  }
  ${PLATFORM_SETTING_FIELDS}
`;
