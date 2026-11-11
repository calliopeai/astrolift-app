import { gql } from "@apollo/client";
import { NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS } from "./nvke-install-plan-node-platforms.queries";
import { NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS } from "./nvke-install-plan-pull-secret-analysis.queries";

export const REVIEW_NVKE_INSTALL_PLAN_PULL_RUNTIME = gql`
  mutation ReviewNvkeInstallPlanPullRuntime(
    $input: NvkeOperatorInstallPlanPullRuntimeReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanPullRuntime(input: $input) {
      ok
      errors {
        code
      }
      data {
        correspondenceDigest
        imageUseCount
        nodeCount
        sourceObservedAt
        nodeObservedAt
        executionState
        continuationAuthorized
        analysis {
          ...NvkeInstallPlanPullSecretAnalysisFields
        }
        nodePlatforms {
          ...NvkeInstallPlanNodePlatformsFields
        }
      }
    }
  }
  ${NVKE_INSTALL_PLAN_PULL_SECRET_ANALYSIS_FIELDS}
  ${NVKE_INSTALL_PLAN_NODE_PLATFORMS_FIELDS}
`;
