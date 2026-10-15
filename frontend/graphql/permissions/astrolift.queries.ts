import { gql } from "@apollo/client";

export const GET_MY_PERMISSIONS = gql`
  query GetMyPermissions {
    astroliftMyPermissions
  }
`;

/** Moves when the viewer's roles, attributes, policies or session do (#1867). */
export const GET_ACCESS_REVISION = gql`
  query GetAccessRevision {
    astroliftAccessRevision
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
