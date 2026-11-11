import { gql } from "@apollo/client";
import {
  NVKE_INSTALL_PLAN_IMAGE_PAYLOAD_FIELDS,
  NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION_FIELDS,
} from "./nvke-install-plan-immutable.queries";

export const PREPARE_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD = gql`
  mutation PrepareNvkeInstallPlanImagePayload(
    $input: PrepareNvkeOperatorInstallPlanImmutableInput!
  ) {
    prepareNvkeOperatorInstallPlanImagePayload(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImagePayloadFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_PAYLOAD_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD = gql`
  mutation ReviewNvkeInstallPlanImagePayload($input: NvkeOperatorInstallPlanImmutableReviewInput!) {
    reviewNvkeOperatorInstallPlanImagePayload(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImagePayloadFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_PAYLOAD_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_IMAGE_PAYLOAD = gql`
  mutation RetryNvkeInstallPlanImagePayload($input: NvkeOperatorInstallPlanImmutableReviewInput!) {
    recoverNvkeOperatorInstallPlanImagePayloadPreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImagePayloadFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMAGE_PAYLOAD_FIELDS}
`;

export const PREPARE_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION = gql`
  mutation PrepareNvkeInstallPlanImmutableExecutionTree(
    $input: PrepareNvkeOperatorInstallPlanImmutableInput!
  ) {
    prepareNvkeOperatorInstallPlanImmutableExecutionTree(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImmutableExecutionFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION_FIELDS}
`;

export const REVIEW_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION = gql`
  mutation ReviewNvkeInstallPlanImmutableExecutionTree(
    $input: NvkeOperatorInstallPlanImmutableReviewInput!
  ) {
    reviewNvkeOperatorInstallPlanImmutableExecutionTree(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImmutableExecutionFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION_FIELDS}
`;

export const RETRY_NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION = gql`
  mutation RetryNvkeInstallPlanImmutableExecutionTree(
    $input: NvkeOperatorInstallPlanImmutableReviewInput!
  ) {
    recoverNvkeOperatorInstallPlanImmutableExecutionTreePreparation(input: $input) {
      ok
      errors {
        code
      }
      data {
        ...NvkeInstallPlanImmutableExecutionFields
      }
    }
  }
  ${NVKE_INSTALL_PLAN_IMMUTABLE_EXECUTION_FIELDS}
`;
