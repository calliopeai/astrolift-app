import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_CHARTS_FIELDS } from "./nvke-install-plan-charts.queries";

export const PREPARE_NVKE_INSTALL_PLAN_CHARTS = gql`
  mutation PrepareNvkeInstallPlanCharts($input: PrepareNvkeOperatorInstallPlanChartsInput!) {
    prepareNvkeOperatorInstallPlanCharts(input: $input) {
      ok
      errors { code }
      data { ...NvkeInstallPlanChartsFields }
    }
  }
  ${NVKE_INSTALL_PLAN_CHARTS_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_CHARTS = gql`
  mutation ReviewNvkeInstallPlanCharts($input: NvkeOperatorInstallPlanChartsReviewInput!) {
    reviewNvkeOperatorInstallPlanCharts(input: $input) {
      ok
      errors { code }
      data { ...NvkeInstallPlanChartsFields }
    }
  }
  ${NVKE_INSTALL_PLAN_CHARTS_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_CHARTS = gql`
  mutation RetryNvkeInstallPlanCharts($input: NvkeOperatorInstallPlanChartsReviewInput!) {
    recoverNvkeOperatorInstallPlanChartsPreparation(input: $input) {
      ok
      errors { code }
      data { ...NvkeInstallPlanChartsFields }
    }
  }
  ${NVKE_INSTALL_PLAN_CHARTS_FIELDS}
`;
