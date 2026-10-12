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
