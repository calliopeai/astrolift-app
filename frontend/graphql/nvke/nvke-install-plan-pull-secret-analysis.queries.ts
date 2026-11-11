import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS = gql`
  fragment NvkeInstallPlanPullSecretAnalysisFields on NvkeOperatorInstallPlanPullSecretAnalysisType {
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
    analyzedAt
    sourceObservedAt
    pullAuthorityId
    pullAuthorityVersion
    pullAuthorityDigest
    executionState
    continuationAuthorized
    authorityDigest
    imageUseCount
    candidateCount
    missingReferenceCount
  }
`;
