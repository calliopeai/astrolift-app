import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_IMAGE_FIELDS = gql`
  fragment NvkeInstallPlanImageFields on NvkeOperatorInstallPlanImageType {
    id
    version
    status
    sourceKind
    planId
    planVersion
    planDigest
    operationId
    operationVersion
    siteId
    siteVersion
    authorityId
    authorityVersion
    chartArtifactId
    chartArtifactVersion
    chartArtifactDigest
    treeId
    treeVersion
    treeDigest
    artifactDigest
    sourceDigest
    captureDigest
    rootDigest
    bytes
    blobCount
    observedAt
    executionState
    continuationAuthorized
  }
`;
