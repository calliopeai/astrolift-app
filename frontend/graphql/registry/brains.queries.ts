import { gql } from "@apollo/client";

/**
 * Hosted semantic layers (brain apps) the viewer can read (#2320): the same
 * visibility, team shares and token ceiling as apps.
 */
export const LIST_BRAINS = gql`
  query ListBrains($search: String) {
    astroliftBrains(search: $search) {
      id
      slug
      name
      kind
      team
      project
      environments {
        name
        url
      }
      sharedWith {
        team
        access
      }
    }
  }
`;
