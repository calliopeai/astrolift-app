import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_VERIFICATION_FIELDS = gql`
  fragment NvkeInstallPlanVerificationFields on NvkeOperatorInstallPlanVerificationType {
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
    contextId
    contextVersion
    artifactDigest
    verificationDigest
    requests
    bytes
    observedAt
    executionState
    continuationAuthorized
  }
`;
