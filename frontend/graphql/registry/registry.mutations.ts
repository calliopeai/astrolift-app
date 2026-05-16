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
        cronExpression
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

const APP_TEAM_ACCESS_FIELDS = `
  id
  appId
  appSlug
  teamId
  teamSlug
  teamName
  accessLevel
  isHome
  createdAt
  updatedAt
`;

export const MOVE_APP_TO_TEAM = gql`
  mutation MoveAppToTeam($input: MoveAppToTeamInput!) {
    moveAppToTeam(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        teamSlug
        projectSlug
      }
    }
  }
`;

export const GRANT_TEAM_ACCESS_TO_APP = gql`
  mutation GrantTeamAccessToApp($input: GrantTeamAccessInput!) {
    grantTeamAccessToApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${APP_TEAM_ACCESS_FIELDS}
      }
    }
  }
`;

export const REVOKE_TEAM_ACCESS_FROM_APP = gql`
  mutation RevokeTeamAccessFromApp($input: RevokeTeamAccessInput!) {
    revokeTeamAccessFromApp(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        deleted
      }
    }
  }
`;

/**
 * Re-fetch the deploy branch's astrolift.toml and reconcile workloads,
 * env, managed services, and schedules — the "Resync from source"
 * button on the Settings landing (#386). Non-destructive on staged
 * drafts; surfaces a one-line summary in the success toast.
 */
export const RESYNC_MANIFEST_FROM_REPO = gql`
  mutation ResyncAstroliftManifestFromRepo($input: ResyncManifestFromRepoInput!) {
    resyncAstroliftManifestFromRepo(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        syncState
        summary
        workloadsAdded
        workloadsRemoved
        workloadsChanged
        managedServicesAdded
        managedServicesRemoved
        envKeysChanged
        schedulesChanged
      }
    }
  }
`;
