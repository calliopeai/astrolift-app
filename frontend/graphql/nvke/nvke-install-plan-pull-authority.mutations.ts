import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_PULL_AUTHORITY_FIELDS } from "./nvke-install-plan-pull-authority.queries";

export const PREPARE_NVKE_INSTALL_PLAN_PULL_AUTHORITY = gql`
  mutation PrepareNvkeInstallPlanPullAuthority(
    $input: PrepareNvkeOperatorInstallPlanPullAuthorityInput!
  ) {
    prepareNvkeOperatorInstallPlanPullAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanPullAuthorityFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_AUTHORITY_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_PULL_AUTHORITY = gql`
  mutation ReviewNvkeInstallPlanPullAuthority(
    $input: NvkeOperatorInstallPlanPullAuthorityReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanPullAuthority(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanPullAuthorityFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_AUTHORITY_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_PULL_AUTHORITY = gql`
  mutation RetryNvkeInstallPlanPullAuthority(
    $input: NvkeOperatorInstallPlanPullAuthorityReviewInput!
  ) {
    recoverNvkeOperatorInstallPlanPullAuthorityPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanPullAuthorityFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_AUTHORITY_FIELDS}
`;
