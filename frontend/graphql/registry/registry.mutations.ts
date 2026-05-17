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
        requiresApproval
        approverTeamId
        approverUserIds
        minimumApprovals
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
        currentVersion
        requestedVersion
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
        version
      }
    }
  }
`;

/**
 * Rename the platform-managed subdomain for an app (#408). The
 * backend enforces uniqueness within the install and re-issues DNS;
 * the FE just refetches `GET_APP` so the new host string flows
 * through topology, the URL card, and the Open button.
 */
export const SET_APP_SUBDOMAIN = gql`
  mutation SetAppSubdomain($input: SetAppSubdomainInput!) {
    setAppSubdomain(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        subdomain
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
 * Re-parent an app to a project (or unassign when `projectGuid: null`).
 * Powers the Settings landing "Assign project" card (#391). The
 * backend follows the project's team so the nav tree stays coherent;
 * the FE just refetches `GET_APP` + the nav-tree query so the new
 * grouping renders immediately.
 */
export const ASSIGN_APP_TO_PROJECT = gql`
  mutation AssignAstroliftAppToProject($input: AssignAppToProjectInput!) {
    assignAstroliftAppToProject(input: $input) {
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
        teamName
        teamId
        projectSlug
        projectName
        projectId
      }
    }
  }
`;

const APP_WEBHOOK_PAUSE_FIELDS = `
  id
  slug
  webhookDeploysPaused
  webhookDeploysPausedAt
  webhookDeploysPausedByEmail
  webhookDeploysPauseReason
`;

/**
 * Pause app-global webhook-fired deploys (#399). Stops the deploy
 * storm from CI / push / scheduled triggers across every environment
 * without paging through each env's deploys_paused or pausing ingress.
 * Manual operator deploys continue to flow — explicit on-call escape
 * valve. Optional `reason` is recorded on the row + audit log.
 */
export const PAUSE_APP_WEBHOOK_DEPLOYS = gql`
  mutation PauseAstroliftAppWebhookDeploys($input: PauseAppWebhookDeploysInput!) {
    pauseAstroliftAppWebhookDeploys(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${APP_WEBHOOK_PAUSE_FIELDS}
      }
    }
  }
`;

/**
 * Lift the app-global webhook-deploy pause (#399). Clears the
 * audit columns on the row; the audit log retains the history.
 */
export const RESUME_APP_WEBHOOK_DEPLOYS = gql`
  mutation ResumeAstroliftAppWebhookDeploys($input: ResumeAppWebhookDeploysInput!) {
    resumeAstroliftAppWebhookDeploys(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${APP_WEBHOOK_PAUSE_FIELDS}
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
