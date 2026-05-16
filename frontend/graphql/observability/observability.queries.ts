/**
 * App > Observability golden-signals queries (#380).
 *
 * Backed by the cluster-side Prometheus stack via the
 * ``astrolift_observability`` resolver. Both queries degrade to an
 * empty list / null when Prometheus isn't reachable or the cluster has
 * no ``prometheus_endpoint`` configured — the UI surfaces that as
 * "metrics not yet flowing".
 */

import { gql } from "@apollo/client";

const TIME_SERIES_POINT_FIELDS = `
  ts
  value
`;

export const GET_APP_GOLDEN_SIGNALS = gql`
  query GetAppGoldenSignals(
    $appSlug: String!
    $environmentName: String
    $rangeSeconds: Int
  ) {
    astroliftAppGoldenSignals(
      appSlug: $appSlug
      environmentName: $environmentName
      rangeSeconds: $rangeSeconds
    ) {
      name
      rangeSeconds
      unit
      promql
      samples {
        ${TIME_SERIES_POINT_FIELDS}
      }
    }
  }
`;

export const GET_APP_STATUS_CODE_BREAKDOWN = gql`
  query GetAppStatusCodeBreakdown(
    $appSlug: String!
    $environmentName: String
    $rangeSeconds: Int
  ) {
    astroliftAppStatusCodeBreakdown(
      appSlug: $appSlug
      environmentName: $environmentName
      rangeSeconds: $rangeSeconds
    ) {
      rangeSeconds
      promql
      series {
        codeClass
        topCodes
        samples {
          ${TIME_SERIES_POINT_FIELDS}
        }
      }
    }
  }
`;
