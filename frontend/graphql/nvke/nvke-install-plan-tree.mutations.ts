import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_TREE_FIELDS } from "./nvke-install-plan-tree.queries";

export const PREPARE_NVKE_INSTALL_PLAN_TREE = gql`
  mutation PrepareNvkeInstallPlanTree($input: PrepareNvkeOperatorInstallPlanTreeInput!) {
    prepareNvkeOperatorInstallPlanTree(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanTreeFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_TREE_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_TREE = gql`
  mutation ReviewNvkeInstallPlanTree($input: NvkeOperatorInstallPlanTreeReviewInput!) {
    reviewNvkeOperatorInstallPlanTree(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanTreeFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_TREE_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_TREE = gql`
  mutation RetryNvkeInstallPlanTree($input: NvkeOperatorInstallPlanTreeReviewInput!) {
    recoverNvkeOperatorInstallPlanTreePreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanTreeFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_TREE_FIELDS}
`;
