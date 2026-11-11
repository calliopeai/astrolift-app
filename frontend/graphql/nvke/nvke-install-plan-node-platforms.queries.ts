import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS = gql`
  fragment NvkeInstallPlanNodePlatformsFields on NvkeOperatorInstallPlanNodePlatformsType {
    id
    version
    status
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
    workloadId
    workloadVersion
    workloadDigest
    bindingId
    bindingVersion
    bindingDigest
    artifactDigest
    sourceDigest
    captureDigest
    observedAt
    executionState
    continuationAuthorized
    platformDigest
    nodeCount
    contentCount
    imageReferenceCount
    pairCount
    issueCount
  }
`;
