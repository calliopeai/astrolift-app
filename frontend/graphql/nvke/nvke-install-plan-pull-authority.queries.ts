import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_PULL_AUTHORITY_FIELDS = gql`
  fragment NvkeInstallPlanPullAuthorityFields on NvkeOperatorInstallPlanPullAuthorityType {
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
    authorityDigest
    podCount
    serviceAccountCount
    secretCount
    missingReferenceCount
  }
`;
