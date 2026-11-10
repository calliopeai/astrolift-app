import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_IMAGE_FIELDS } from "./nvke-install-plan-image.queries";

export const PREPARE_NVKE_INSTALL_PLAN_IMAGE = gql`
  mutation PrepareNvkeInstallPlanImage($input: PrepareNvkeOperatorInstallPlanImageInput!) {
    prepareNvkeOperatorInstallPlanImage(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImageFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_IMAGE = gql`
  mutation ReviewNvkeInstallPlanImage($input: NvkeOperatorInstallPlanImageReviewInput!) {
    reviewNvkeOperatorInstallPlanImage(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImageFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_IMAGE = gql`
  mutation RetryNvkeInstallPlanImage($input: NvkeOperatorInstallPlanImageReviewInput!) {
    recoverNvkeOperatorInstallPlanImagePreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImageFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_FIELDS}
`;
