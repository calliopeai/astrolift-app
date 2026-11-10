import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_IMAGE_BINDING_FIELDS = gql`
  fragment NvkeInstallPlanImageBindingFields on NvkeOperatorInstallPlanImageBindingType {
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
    artifactDigest
    sourceDigest
    captureDigest
    workloadId
    workloadVersion
    workloadDigest
    bindingDigest
    imageCount
    imageUseCount
    importCount
    rewriteCount
    verifiedImageBytes
    workloadIssueCount
    observedAt
    executionState
    continuationAuthorized
  }
`;
