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
