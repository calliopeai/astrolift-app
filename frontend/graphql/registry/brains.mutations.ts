import { gql } from "@apollo/client";

/**
 * A 15-minute read token for one brain the caller can read (#2320), with its
 * endpoints and the header the cluster edge admits it in.
 */
export const ISSUE_BRAIN_READ_TOKEN = gql`
  mutation IssueBrainReadToken($slug: String!) {
    issueBrainReadToken(slug: $slug) {
      ok
      errors {
        field
        message
        code
      }
      data {
        id
        slug
        endpoints
        header
        token
        expiresAt
      }
    }
  }
`;
