import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS } from "./nvke-install-plan-node-platforms.queries";

export const PREPARE_NVKE_INSTALL_PLAN_NODE_PLATFORMS = gql`
  mutation PrepareNvkeInstallPlanNodePlatforms(
    $input: PrepareNvkeOperatorInstallPlanNodePlatformsInput!
  ) {
    prepareNvkeOperatorInstallPlanNodePlatforms(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanNodePlatformsFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_NODE_PLATFORMS = gql`
  mutation ReviewNvkeInstallPlanNodePlatforms(
    $input: NvkeOperatorInstallPlanNodePlatformsReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanNodePlatforms(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanNodePlatformsFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_NODE_PLATFORMS = gql`
  mutation RetryNvkeInstallPlanNodePlatforms(
    $input: NvkeOperatorInstallPlanNodePlatformsReviewInput!
  ) {
    recoverNvkeOperatorInstallPlanNodePlatformsPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanNodePlatformsFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS}
`;
