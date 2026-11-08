import { gql } from "@apollo/client";
import { NVKE_OPERATOR_AUTHORITY_FIELDS } from "./nvke-operator.queries";
import { NVKE_OPERATION_FIELDS } from "./nvke.queries";

export const CONFIGURE_NVKE_OPERATOR_AUTHORITY = gql`
  mutation ConfigureNvkeOperatorAuthority($input: ConfigureNvkeOperatorAuthorityInput!) {
    configureNvkeOperatorAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperatorAuthorityFields
      }
    }
  }
  ${NVKE_OPERATOR_AUTHORITY_FIELDS}
`;

export const VERIFY_NVKE_OPERATOR_AUTHORITY = gql`
  mutation VerifyNvkeOperatorAuthority($input: NvkeOperatorAuthorityReviewInput!) {
    verifyNvkeOperatorAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperatorAuthorityFields
      }
    }
  }
  ${NVKE_OPERATOR_AUTHORITY_FIELDS}
`;

export const SET_NVKE_OPERATOR_AUTHORITY_ENABLED = gql`
  mutation SetNvkeOperatorAuthorityEnabled($input: SetNvkeOperatorAuthorityEnabledInput!) {
    setNvkeOperatorAuthorityEnabled(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperatorAuthorityFields
      }
    }
  }
  ${NVKE_OPERATOR_AUTHORITY_FIELDS}
`;

export const REMOVE_NVKE_OPERATOR_AUTHORITY = gql`
  mutation RemoveNvkeOperatorAuthority($input: NvkeOperatorAuthorityReviewInput!) {
    removeNvkeOperatorAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperatorAuthorityFields
      }
    }
  }
  ${NVKE_OPERATOR_AUTHORITY_FIELDS}
`;

export const REQUEST_NVKE_OPERATOR_JOB = gql`
  mutation RequestNvkeOperatorJob($input: RequestNvkeOperatorJobInput!) {
    requestNvkeOperatorJob(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const RECONCILE_NVKE_OPERATOR_JOB = gql`
  mutation ReconcileNvkeOperatorJob($input: NvkeOperatorJobReviewInput!) {
    reconcileNvkeOperatorJob(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const STOP_NVKE_OPERATOR_JOB = gql`
  mutation StopNvkeOperatorJob($input: NvkeOperatorJobReviewInput!) {
    stopNvkeOperatorJob(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const REVIEW_NVKE_OPERATOR_INSTALL = gql`
  mutation ReviewNvkeOperatorInstall($input: NvkeOperatorJobReviewInput!) {
    reviewNvkeOperatorInstall(input: $input) {
      ok
      errors {
        code
      }
      data {
        snapshotId
        snapshotExpiresAt
        workloadCount
        siteId
        siteVersion
        operationId
        operationVersion
        authorityId
        authorityVersion
        instanceId
        bundleManifestSha256
        nativeRevision
        nativeRunId
        reviewDigest
        observedAt
        originalStartedAt
        nativeArgumentsSha256
        nativeExitCode
        apiReady
        continuationAuthorized
        reviewerSha256
        qualificationHelperSha256
        evidence {
          guardSha256
          startedSha256
          finishedSha256
          stdoutSha256
          stderrSha256
        }
      }
    }
  }
`;
