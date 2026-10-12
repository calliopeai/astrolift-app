import { gql } from "@apollo/client";

// Public, unauthenticated install handshake (#479). We only pull the
// feature-flag allow-list here — the UI gates surfaces on these keys
// (e.g. `zentinelle.enabled` gates the Zentinelle-badged agent tabs).
export const SERVER_INFO = gql`
  query AstroliftServerInfo {
    astroliftServerInfo {
      featureFlags {
        key
        enabled
      }
    }
  }
`;

// Richer read for the admin feature-flipper screen
// (/administration/features): the runtime flags WITH their descriptions
// plus the read-only install-time (build-time) feature inventory. Kept
// separate from SERVER_INFO so the hot-path nav gate query stays small.
export const ADMIN_FEATURE_INVENTORY = gql`
  query AdminFeatureInventory {
    astroliftServerInfo {
      featureFlags {
        key
        enabled
        description
      }
      buildTimeFeatures {
        key
        enabled
        envVar
        description
      }
    }
  }
`;

export const PLATFORM_SETTING_FIELDS = gql`
  fragment PlatformSettingFields on AstroliftPlatformSetting {
    key
    group
    kind
    helpText
    value
    default
    changed
    choices
    editable
    readOnlyReason
    warning
  }
`;

// Every install setting for Administration › Platform settings (#1861).
// Platform operator only; a credential's value is never returned.
export const PLATFORM_SETTINGS = gql`
  query PlatformSettings {
    astroliftPlatformSettings {
      ...PlatformSettingFields
    }
  }
  ${PLATFORM_SETTING_FIELDS}
`;
