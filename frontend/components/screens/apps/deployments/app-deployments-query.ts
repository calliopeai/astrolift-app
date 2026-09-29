/**
 * The app Deployments tab's page query. Not a "use client" module, so the
 * route can preload it.
 */
import { gql } from "@apollo/client";

export const APP_DEPLOYMENTS_OPERATION = "AppDeploymentsPage";

/**
 * One app's deployments, a page at a time. `astroliftDeploymentsPage` as the
 * fleet list walks it, with the row fields the app tab shows that the shared
 * `DeploymentFields` fragment leaves out (`statusReason`, `buildError`, the
 * manifest resync pair: why a deploy failed or waits, on the row, #2123).
 * Inline, like the fleet list's tab counts, so the shared fragment and the
 * committed codegen stay as they are.
 */
export const APP_DEPLOYMENTS_PAGE = gql`
  query AppDeploymentsPage(
    $appSlug: String
    $environmentName: String
    $statuses: [String!]
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftDeploymentsFilter
    $sort: String
  ) {
    astroliftDeploymentsPage(
      appSlug: $appSlug
      environmentName: $environmentName
      statuses: $statuses
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
    ) {
      items {
        id
        registeredAppSlug
        environmentName
        workloadSlug
        triggerKind
        strategy
        status
        imageTag
        imageDigest
        clusterRevision
        approvalsRequired
        approvalsReceived
        requiredApproverCount
        startedAt
        succeededAt
        failedAt
        endedAt
        durationSeconds
        createdAt
        commitSha
        commitMessage
        commitAuthor
        commitAuthorAvatarUrl
        branch
        prNumber
        prUrl
        ciActorKind
        ciProvider
        ciRunUrl
        repoUrl
        abortedReason
        manifestResyncStatus
        manifestResyncError
        buildError
        statusReason
        triggeredByUserId
        triggeredByMe
        approvedBy {
          userId
          displayName
          email
          approvedAt
          mailtoUrl
        }
        awaitingApprovers {
          userId
          displayName
          email
          approvedAt
          mailtoUrl
        }
      }
      nextCursor
      totalCount
    }
  }
`;
