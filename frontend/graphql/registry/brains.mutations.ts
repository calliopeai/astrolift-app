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

/**
 * An app hosted on a brain (#2243), from the brain-app template. Each deploy
 * gives it the brain's MCP URLs and a read token as BRAIN_* env.
 */
export const HOST_APP_ON_BRAIN = gql`
  mutation HostAppOnBrain($input: HostAppOnBrainInput!) {
    hostAppOnBrain(input: $input) {
      ok
      errors {
        field
        message
        code
      }
      data {
        brainSlug
        environmentName
        app {
          id
          slug
        }
      }
    }
  }
`;
