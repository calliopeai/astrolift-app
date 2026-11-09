import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_FIELDS } from "./nvke-install-plan.queries";

export const PREPARE_NVKE_INSTALL_PLAN = gql`
  mutation PrepareNvkeInstallPlan($input: PrepareNvkeOperatorInstallPlanInput!) {
    prepareNvkeOperatorInstallPlan(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN = gql`
  mutation ReviewNvkeInstallPlan($input: NvkeOperatorInstallPlanReviewInput!) {
    reviewNvkeOperatorInstallPlan(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN = gql`
  mutation RetryNvkeInstallPlan($input: NvkeOperatorInstallPlanReviewInput!) {
    recoverNvkeOperatorInstallPlanPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_FIELDS}
`;
