import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS } from "./nvke-install-plan-pull-secret-analysis.queries";

export const PREPARE_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS = gql`
  mutation PrepareNvkeInstallPlanPullSecretAnalysis(
    $input: PrepareNvkeOperatorInstallPlanPullSecretAnalysisInput!
  ) {
    prepareNvkeOperatorInstallPlanPullSecretAnalysis(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanPullSecretAnalysisFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS = gql`
  mutation ReviewNvkeInstallPlanPullSecretAnalysis(
    $input: NvkeOperatorInstallPlanPullSecretAnalysisReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanPullSecretAnalysis(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanPullSecretAnalysisFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS = gql`
  mutation RetryNvkeInstallPlanPullSecretAnalysis(
    $input: NvkeOperatorInstallPlanPullSecretAnalysisReviewInput!
  ) {
    recoverNvkeOperatorInstallPlanPullSecretAnalysisPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanPullSecretAnalysisFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS}
`;
