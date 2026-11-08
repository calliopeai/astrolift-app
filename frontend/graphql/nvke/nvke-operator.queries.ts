import { gql } from "@apollo/client";

export const NVKE_OPERATOR_AUTHORITY_FIELDS = gql`
  fragment NvkeOperatorAuthorityFields on NvkeOperatorAuthorityType {
    id
    version
    siteId
    siteVersion
    endpoint
    instanceId
    bundleManifestSha256
    isEnabled
    timeoutSeconds
    hasCredentials
    verifiedAt
    verificationCode
    deletedAt
  }
`;

export const GET_NVKE_OPERATOR_AUTHORITY = gql`
  query GetNvkeOperatorAuthority($siteId: GUID!) {
    astroliftNvkeOperatorAuthority(siteId: $siteId) {
      ...NvkeOperatorAuthorityFields
    }
  }
  ${NVKE_OPERATOR_AUTHORITY_FIELDS}
`;

export const GET_NVKE_OPERATOR_CONTRACT = gql`
  query GetNvkeOperatorContract {
    astroliftNvkeOperatorContract {
      revision
      commands {
        key
        positionals
        infrastructureChange
        options {
          name
          kind
          required
          choices
        }
      }
    }
  }
`;

export const GET_NVKE_OPERATOR_ARTIFACT_TEXT = gql`
  query GetNvkeOperatorArtifactText($input: ReadNvkeOperatorArtifactInput!) {
    astroliftNvkeOperatorArtifactText(input: $input) {
      ok
      errors {
        code
      }
      data
    }
  }
`;

export const GET_NVKE_OPERATOR_JOB_PAGE = gql`
  query GetNvkeOperatorJobPage(
    $input: NvkeOperatorJobReviewInput!
    $after: String
    $limit: Int = 50
    $search: String = ""
  ) {
    astroliftNvkeOperatorJobPage(input: $input, after: $after, limit: $limit, search: $search) {
      ok
      errors {
        code
      }
      data {
        siteId
        siteVersion
        operationId
        operationVersion
        authorityId
        authorityVersion
        instanceId
        bundleManifestSha256
        nativeRevision
        command
        found
        state
        effectful
        cancellationRequested
        nativeExitCode
        errorCode
        reconciledFromNativeReceipt
        receiptSha256
        items {
          name
          size
          sha256
        }
        nextCursor
        totalCount
      }
    }
  }
`;
