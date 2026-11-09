import { gql } from "@apollo/client";

export const NVKE_INSTALL_PLAN_FIELDS = gql`
  fragment NvkeInstallPlanFields on NvkeOperatorInstallPlanType {
    id
    version
    status
    operationId
    operationVersion
    siteId
    siteVersion
    authorityId
    authorityVersion
    itemCount
    retainedBytes
    proposedBytes
    plan {
      format
      scope
      id
      preparedAt
      phaseEvidence
      requiresOriginalSourceVerification
      requiresExplicitAcknowledgement
      continuationAuthorized
      firstRemainingOrdinal
      planDigest
      source {
        siteId
        instanceId
        jobId
        nativeRunId
        nativeRevision
        nativeExitCode
        bundleManifestSha256
        nativeArgumentsSha256
        retainedInputsSha256
        proposedInputsSha256
        proposedExecutionSha256
        privateSourceSha256
        reviewDigest
        stderrSha256
        originalStartedAt
        reviewObservedAt
        originalDeploymentEvidence
      }
    }
    infrastructure {
      apiReady
      observedAt
      clusterUid
      workloadCount
    }
  }
`;

export const GET_NVKE_INSTALL_PLAN_ITEM_PAGE = gql`
  query GetNvkeInstallPlanItemPage(
    $input: NvkeOperatorInstallPlanReviewInput!
    $planDigest: String!
    $after: String
    $limit: Int = 25
    $search: String = ""
  ) {
    astroliftNvkeOperatorInstallPlanItemPage(
      input: $input
      planDigest: $planDigest
      after: $after
      limit: $limit
      search: $search
    ) {
      ok
      errors {
        code
      }
      data {
        planId
        planDigest
        view
        nextCursor
        totalCount
        items {
          name
          type
          originalType
          contentSha256
          originalContentSha256
          ordinal
          originalObservation
          proposedAction
        }
      }
    }
  }
`;

export const GET_NVKE_INSTALL_PLAN_WORKLOAD_PAGE = gql`
  query GetNvkeInstallPlanWorkloadPage(
    $input: NvkeOperatorInstallPlanReviewInput!
    $planDigest: String!
    $after: String
    $limit: Int = 25
    $search: String = ""
  ) {
    astroliftNvkeOperatorInstallPlanWorkloadPage(
      input: $input
      planDigest: $planDigest
      after: $after
      limit: $limit
      search: $search
    ) {
      ok
      errors {
        code
      }
      data {
        planId
        planDigest
        view
        nextCursor
        totalCount
        items {
          namespace
          kind
          name
          generation
          observedGeneration
          desiredReplicas
          readyReplicas
          updatedReplicas
          observation
        }
      }
    }
  }
`;
