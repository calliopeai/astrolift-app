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
    }
  }
`;

export const LIST_BUDGETS = gql`
  query ListBudgets {
    astroliftBudgets {
      id
      scopeKind
      scopeId
      amountCents
      currency
      period
      currentSpendCents
      alertsAtPct
    }
  }
`;

export const LIST_COST_SNAPSHOTS = gql`
  query ListCostSnapshots($days: Int, $limit: Int) {
    astroliftCostSnapshots(days: $days, limit: $limit) {
      id
      projectId
      registeredAppId
      takenAt
      by
      amountCents
      currency
      source
    }
  }
`;
