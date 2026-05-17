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

export const REQUEST_QUOTA_INCREASE = gql`
  mutation RequestQuotaIncrease($input: RequestQuotaIncreaseInput!) {
    requestQuotaIncrease(input: $input) {
      ok
      errors { code message field }
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
  query ListCostSnapshots($days: Int, $window: CostWindow, $limit: Int) {
    astroliftCostSnapshots(days: $days, window: $window, limit: $limit) {
      id
      projectId
      registeredAppId
      managedServiceBindingId
      takenAt
      by
      amountCents
      currency
      source
    }
  }
`;

export const GET_COST_TREND = gql`
  query GetCostTrend($window: CostWindow, $days: Int) {
    astroliftCostTrend(window: $window, days: $days) {
      date
      amountCents
      currency
      isAnomaly
    }
  }
`;

export const GET_COST_FORECAST = gql`
  query GetCostForecast {
    astroliftCostForecast {
      mtdCents
      previousMonthCents
      deltaPct
      projectedMonthlyCents
      confidence
      currency
    }
  }
`;

export const GET_COST_BY_BINDING = gql`
  query GetCostByBinding(
    $window: CostWindow
    $days: Int
    $registeredAppSlug: String
  ) {
    astroliftCostByBinding(
      window: $window
      days: $days
      registeredAppSlug: $registeredAppSlug
    ) {
      attributedRows {
        managedServiceBindingId
        managedServiceId
        managedServiceName
        managedServiceKind
        registeredAppSlug
        by
        amountCents
        currency
      }
      unattributedCents
      totalCents
      currency
    }
  }
`;
