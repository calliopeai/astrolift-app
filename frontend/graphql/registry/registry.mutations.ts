import { gql } from "@apollo/client";

export const REGISTER_APP = gql`
  mutation RegisterApp($input: RegisterAppInput!) {
    registerApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        provisioningStatus
        sourceRepo
        manifestPath
        teamSlug
        projectSlug
      }
    }
  }
`;

export const UPDATE_APP = gql`
  mutation UpdateApp($input: UpdateAppInput!) {
    updateApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        description
        sourceUrl
        manifestPath
        defaultBranch
        deployBranch
        triggerMode
        previewEnabled
        isActive
      }
    }
  }
`;

export const SOFT_DELETE_APP = gql`
  mutation SoftDeleteApp($input: SoftDeleteAppInput!) {
    softDeleteApp(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

const MANIFEST_STAGE_FIELDS = `
  id
  syncState
  rawManifest
  rawManifestStaged
`;

export const UPDATE_MANIFEST = gql`
  mutation UpdateManifest($input: UpdateManifestInput!) {
    updateManifest(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${MANIFEST_STAGE_FIELDS}
      }
    }
  }
`;

export const SYNC_MANIFEST_FROM_REPO = gql`
  mutation SyncManifestFromRepo($input: SyncManifestFromRepoInput!) {
    syncManifestFromRepo(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${MANIFEST_STAGE_FIELDS}
      }
    }
  }
`;

export const PUSH_MANIFEST_TO_REPO = gql`
  mutation PushManifestToRepo($input: PushManifestToRepoInput!) {
    pushManifestToRepo(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        prUrl
        branchName
        note
      }
    }
  }
`;
