import { gql } from "@apollo/client";

import { BRAIN_CREATION_FIELDS, BRAIN_FIELDS, BRAIN_SUBSTRATE_FIELDS } from "./brains.queries";

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

/**
 * Create a semantic layer (#2424): checked at once, then scaffolded,
 * registered and deployed in the background; poll the creation it returns.
 * Deploying to production asks for a fresh sign-in (deploy.production).
 */
export const CREATE_BRAIN = gql`
  ${BRAIN_CREATION_FIELDS}
  mutation CreateBrain($input: CreateBrainInput!) {
    createBrain(input: $input) {
      ok
      errors {
        code
        message
        field
        approvalRequestId
        supportedMethods
      }
      data {
        ...BrainCreationFields
      }
    }
  }
`;

/** Mimwell's graduation (#2424): a new cell, committed and applied; the push deploys it. */
export const UPDATE_BRAIN_SUBSTRATE = gql`
  ${BRAIN_FIELDS}
  ${BRAIN_SUBSTRATE_FIELDS}
  mutation UpdateBrainSubstrate($input: UpdateBrainSubstrateInput!) {
    updateBrainSubstrate(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...BrainFields
        substrate {
          ...BrainSubstrateFields
        }
      }
    }
  }
`;

/** Share a layer with a team as viewer or editor (brain.share: confirmation and a fresh sign-in). */
export const SHARE_BRAIN = gql`
  ${BRAIN_FIELDS}
  mutation ShareBrain($input: ShareBrainInput!) {
    shareBrain(input: $input) {
      ok
      errors {
        code
        message
        field
        approvalRequestId
        supportedMethods
      }
      data {
        ...BrainFields
      }
    }
  }
`;

/** Stop sharing a layer with a team; it only narrows, so it asks for nothing more. */
export const UNSHARE_BRAIN = gql`
  ${BRAIN_FIELDS}
  mutation UnshareBrain($input: UnshareBrainInput!) {
    unshareBrain(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ...BrainFields
      }
    }
  }
`;

/** Redeploy the branch head, or run the knowledge refresh first (ingest). */
export const REBUILD_BRAIN = gql`
  mutation RebuildBrain($input: RebuildBrainInput!) {
    rebuildBrain(input: $input) {
      ok
      errors {
        code
        message
        field
        approvalRequestId
        supportedMethods
      }
      data {
        slug
        mode
        deploymentId
        runUrl
        ingestRunUrl
        rebuilds
      }
    }
  }
`;

/**
 * Archive a layer (brain.delete): scaled to zero, out of the register, hosted
 * apps' tokens revoked. Its data and repository are kept; restoreApp brings it back.
 */
export const ARCHIVE_BRAIN = gql`
  ${BRAIN_FIELDS}
  mutation ArchiveBrain($input: ArchiveBrainInput!) {
    archiveBrain(input: $input) {
      ok
      errors {
        code
        message
        field
        approvalRequestId
        supportedMethods
      }
      data {
        ...BrainFields
      }
    }
  }
`;
