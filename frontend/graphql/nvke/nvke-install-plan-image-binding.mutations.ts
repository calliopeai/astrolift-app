import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_IMAGE_BINDING_FIELDS } from "./nvke-install-plan-image-binding.queries";

export const PREPARE_NVKE_INSTALL_PLAN_IMAGE_BINDING = gql`
  mutation PrepareNvkeInstallPlanImageBinding(
    $input: PrepareNvkeOperatorInstallPlanImageBindingInput!
  ) {
    prepareNvkeOperatorInstallPlanImageBinding(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImageBindingFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_BINDING_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_IMAGE_BINDING = gql`
  mutation ReviewNvkeInstallPlanImageBinding(
    $input: NvkeOperatorInstallPlanImageBindingReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanImageBinding(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImageBindingFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_BINDING_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_IMAGE_BINDING = gql`
  mutation RetryNvkeInstallPlanImageBinding(
    $input: NvkeOperatorInstallPlanImageBindingReviewInput!
  ) {
    recoverNvkeOperatorInstallPlanImageBindingPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImageBindingFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_BINDING_FIELDS}
`;
