import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_CHARTS_FIELDS = gql`
  fragment NvkeInstallPlanChartsFields on NvkeOperatorInstallPlanChartsType {
    id
    version
    status
    planId
    planVersion
    operationId
    operationVersion
    siteId
    siteVersion
    authorityId
    authorityVersion
    planDigest
    artifactDigest
    chartCount
    bytes
    expandedBytes
    executionState
    continuationAuthorized
  }
`;
