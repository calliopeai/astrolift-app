import { gql } from "@apollo/client";

export const GET_MY_PERMISSIONS = gql`
  query GetMyPermissions {
    astroliftMyPermissions
  }
`;

/** Easy-mode starting points over the stock roles (#2272). */
export const ACCESS_PRESETS = gql`
  query AccessPresets {
    astroliftAccessPresets {
      key
      label
      summary
      roles {
        scopeLevel
        roleSlug
        tasks {
          key
          label
          granted
        }
      }
    }
  }
`;

/** What each gated action asks of the viewer (#2202, #2203). */
export const ACTION_SAFETY = gql`
  query ActionSafety($appSlug: String) {
    astroliftActionSafety(appSlug: $appSlug) {
      action
      label
      enabled
      confirmation
      verification
      allowSelfApproval
      sources
    }
  }
`;
