import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_WORKLOAD_FIELDS = gql`
  fragment NvkeInstallPlanWorkloadFields on NvkeOperatorInstallPlanWorkloadType {
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
    workloadDigest
    itemCount
    resourceCount
    imageUseCount
    imageReferenceCount
    issueCount
    observedAt
    executionState
    continuationAuthorized
  }
`;
