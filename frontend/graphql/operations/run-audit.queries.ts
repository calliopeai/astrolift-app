import { gql } from "@apollo/client";

/**
 * Everything that ran, of every kind, in one cursor list (#2152): agent
 * tasks, workflow runs, deployments, job runs and task runs. Each kind is
 * read under its own permission and narrowed to the caller's scopes on the
 * server, so a viewer who may not see deployments gets the other kinds.
 * `sort` is `-at` (the default) or `at`; `totalCount` is exact.
 */
export const RUN_AUDIT = gql`
  query RunAudit(
    $filter: AstroliftRunAuditFilter
    $search: String
    $sort: String
    $first: Int!
    $after: String
  ) {
    astroliftRunAudit(filter: $filter, search: $search, sort: $sort, first: $first, after: $after) {
      items {
        kind
        id
        subject
        scope
        agentSlug
        workflowSlug
        projectSlug
        appSlug
        environmentName
        trigger
        sourceTrigger
        startedByKind
        startedById
        startedByDisplay
        startedByMe
        at
        startedAt
        endedAt
        durationSeconds
        status
        outcome
      }
      nextCursor
      totalCount
    }
  }
`;
