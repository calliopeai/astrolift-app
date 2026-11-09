import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_VERIFICATION_FIELDS } from "./nvke-install-plan-verification.queries";

export const PREPARE_NVKE_INSTALL_PLAN_VERIFICATION = gql`
  mutation PrepareNvkeInstallPlanVerification(
    $input: PrepareNvkeOperatorInstallPlanVerificationInput!
  ) {
    prepareNvkeOperatorInstallPlanVerification(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanVerificationFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_VERIFICATION_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_VERIFICATION = gql`
  mutation ReviewNvkeInstallPlanVerification(
    $input: NvkeOperatorInstallPlanVerificationReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanVerification(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanVerificationFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_VERIFICATION_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_VERIFICATION = gql`
  mutation RetryNvkeInstallPlanVerification(
    $input: NvkeOperatorInstallPlanVerificationReviewInput!
  ) {
    recoverNvkeOperatorInstallPlanVerificationPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanVerificationFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_VERIFICATION_FIELDS}
`;
