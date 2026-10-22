import { gql } from "@apollo/client";

export const LIST_QUOTAS = gql`
  query ListQuotas {
    astroliftQuotas {
      id
      scopeKind
      scopeId
      resource
      hardLimit
      softLimit
      currentUsage
      pendingRequest {
        id
        quotaId
        requestedFactor
        reason
        status
        requestedByDisplay
        decidedByDisplay
        decidedAt
        decisionNote
        createdAt
      }
    }
  }
`;

export const QUOTA_USAGE_HISTORY = gql`
  query QuotaUsageHistory($quotaId: GUID!, $windowDays: Int! = 90) {
    astroliftQuotaUsageHistory(quotaId: $quotaId, windowDays: $windowDays) {
      date
      used
      limit
    }
  }
`;

export const REQUEST_QUOTA_INCREASE = gql`
  mutation RequestQuotaIncrease($input: RequestQuotaIncreaseInput!) {
    requestQuotaIncrease(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        quotaId
        requestedFactor
        reason
        status
        requestedByDisplay
        decidedByDisplay
        decidedAt
        decisionNote
        createdAt
      }
    }
  }
`;
