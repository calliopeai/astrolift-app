import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_TREE_FIELDS = gql`
  fragment NvkeInstallPlanTreeFields on NvkeOperatorInstallPlanTreeType {
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
    treeDigest
    fileCount
    bytes
    executionState
    continuationAuthorized
  }
`;
