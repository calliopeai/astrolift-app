import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_WORKLOAD_FIELDS } from "./nvke-install-plan-workload.queries";

export const PREPARE_NVKE_INSTALL_PLAN_WORKLOAD = gql`
  mutation PrepareNvkeInstallPlanWorkload($input: PrepareNvkeOperatorInstallPlanWorkloadInput!) {
    prepareNvkeOperatorInstallPlanWorkload(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanWorkloadFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_WORKLOAD_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_WORKLOAD = gql`
  mutation ReviewNvkeInstallPlanWorkload($input: NvkeOperatorInstallPlanWorkloadReviewInput!) {
    reviewNvkeOperatorInstallPlanWorkload(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanWorkloadFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_WORKLOAD_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_WORKLOAD = gql`
  mutation RetryNvkeInstallPlanWorkload($input: NvkeOperatorInstallPlanWorkloadReviewInput!) {
    recoverNvkeOperatorInstallPlanWorkloadPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanWorkloadFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_WORKLOAD_FIELDS}
`;
