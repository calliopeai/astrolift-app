import { gql } from "@apollo/client";

/** An organization's rule for one gated action (#2202, #2203). */
export const SET_ACTION_SAFETY_RULE = gql`
  mutation SetActionSafetyRule($input: SetActionSafetyRuleInput!) {
    setActionSafetyRule(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        action
        scopeKind
        enabled
        allowSelfApproval
        requireApproval
        minimumApprovals
      }
    }
  }
`;

/** Decide or withdraw an approval request for a gated action (#2202). */
export const APPROVE_ACTION_REQUEST = gql`
  mutation ApproveActionRequest($input: DecideActionApprovalInput!) {
    approveActionRequest(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
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
  }
`;

export const REJECT_ACTION_REQUEST = gql`
  mutation RejectActionRequest($input: DecideActionApprovalInput!) {
    rejectActionRequest(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
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
  }
`;

export const CANCEL_ACTION_REQUEST = gql`
  mutation CancelActionRequest($input: DecideActionApprovalInput!) {
    cancelActionRequest(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
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
  }
`;
