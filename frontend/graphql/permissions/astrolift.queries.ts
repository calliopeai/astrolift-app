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

/**
 * The org's apps with their deploy-approval setting and what the viewer may
 * do on each, for Easy setup's guided approval (#2272).
 */
export const DEPLOY_APPROVAL_APPS = gql`
  query DeployApprovalApps {
    astroliftApps {
      id
      slug
      name
      requiresApproval
      approverTeamId
      approverUserIds
      minimumApprovals
      version
      viewerPermissions
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
      approvable
      approval
      minimumApprovals
      sources
    }
  }
`;

export const ACTION_APPROVAL_REQUESTS = gql`
  query ActionApprovalRequests($status: String) {
    astroliftActionApprovalRequests(status: $status) {
      id
      action
      label
      targetKind
      targetLabel
      appSlug
      requesterEmail
      viewerIsRequester
      status
      requiredApprovals
      approvals
      allowSelfApproval
      createdAt
      expiresAt
    }
  }
`;

export const ACTION_SAFETY_RULES = gql`
  query ActionSafetyRules {
    astroliftActionSafetyRules {
      action
      scopeKind
      scopeId
      scopeLabel
      enabled
      allowSelfApproval
      requireApproval
      minimumApprovals
    }
  }
`;
