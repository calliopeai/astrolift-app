import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_CONTEXT_FIELDS = gql`
  fragment NvkeInstallPlanContextFields on NvkeOperatorInstallPlanContextType {
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
    chartArtifactId
    chartArtifactVersion
    chartArtifactDigest
    treeId
    treeVersion
    treeDigest
    item
    ordinal
    captureDigest
    frozenChartSha256
    contextDigest
    documentCount
    lookupCount
    observedAt
    executionState
    continuationAuthorized
  }
`;
