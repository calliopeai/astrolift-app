import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_CONTEXT_FIELDS } from "./nvke-install-plan-context.queries";

export const PREPARE_NVKE_INSTALL_PLAN_CONTEXT = gql`
  mutation PrepareNvkeInstallPlanContext($input: PrepareNvkeOperatorInstallPlanContextInput!) {
    prepareNvkeOperatorInstallPlanContext(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanContextFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_CONTEXT_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_CONTEXT = gql`
  mutation ReviewNvkeInstallPlanContext($input: NvkeOperatorInstallPlanContextReviewInput!) {
    reviewNvkeOperatorInstallPlanContext(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanContextFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_CONTEXT_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_CONTEXT = gql`
  mutation RetryNvkeInstallPlanContext($input: NvkeOperatorInstallPlanContextReviewInput!) {
    recoverNvkeOperatorInstallPlanContextPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanContextFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_CONTEXT_FIELDS}
`;
