import { gql } from "@apollo/client";
import { NVKE_METRICS_AUTHORITY_FIELDS } from "./nvke-metrics.queries";
import { NVKE_OPERATION_FIELDS } from "./nvke.queries";

export const CONFIGURE_NVKE_METRICS_AUTHORITY = gql`
  mutation ConfigureNvkeMetricsAuthority($input: ConfigureNvkeMetricsAuthorityInput!) {
    configureNvkeMetricsAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeMetricsAuthorityFields
      }
    }
  }
  ${NVKE_METRICS_AUTHORITY_FIELDS}
`;

export const VERIFY_NVKE_METRICS_AUTHORITY = gql`
  mutation VerifyNvkeMetricsAuthority($input: NvkeMetricsAuthorityReviewInput!) {
    verifyNvkeMetricsAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeMetricsAuthorityFields
      }
    }
  }
  ${NVKE_METRICS_AUTHORITY_FIELDS}
`;

export const SET_NVKE_METRICS_AUTHORITY_ENABLED = gql`
  mutation SetNvkeMetricsAuthorityEnabled($input: SetNvkeMetricsAuthorityEnabledInput!) {
    setNvkeMetricsAuthorityEnabled(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeMetricsAuthorityFields
      }
    }
  }
  ${NVKE_METRICS_AUTHORITY_FIELDS}
`;

export const REMOVE_NVKE_METRICS_AUTHORITY = gql`
  mutation RemoveNvkeMetricsAuthority($input: NvkeMetricsAuthorityReviewInput!) {
    removeNvkeMetricsAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeMetricsAuthorityFields
      }
    }
  }
  ${NVKE_METRICS_AUTHORITY_FIELDS}
`;

export const REQUEST_NVKE_CREDENTIAL_OBSERVATION = gql`
  mutation RequestNvkeCredentialObservation($input: RequestNvkeCredentialObservationInput!) {
    requestNvkeCredentialObservation(input: $input) {
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
