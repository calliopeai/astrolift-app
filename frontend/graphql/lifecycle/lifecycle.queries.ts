import { gql } from "@apollo/client";

export const LIST_ENVIRONMENTS = gql`
  query ListEnvironments($appSlug: String) {
    astroliftEnvironments(appSlug: $appSlug) {
      id
      name
      url
      deploysPaused
      ingressPaused
      requiredApprovals
      registeredAppSlug
      clusterSlug
      clusterId
      clusterProviderPluginSlug
      domainZone
      createdAt
      settings {
        id
        key
        value
      }
    }
  }
`;

/**
 * ``LIST_ENVIRONMENTS`` on the §5.1 list contract (#2155): one numbered
 * page with an exact filtered ``totalCount``. ``filter`` takes kind, app,
 * cluster, region and owner ("me" allowed); ``sort`` takes name, app, kind,
 * cluster, region and created, several keys at once.
 */
export const LIST_ENVIRONMENTS_PAGE = gql`
  query ListEnvironmentsPage(
    $appSlug: String
    $search: String
    $filter: AstroliftEnvironmentsFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftEnvironmentsPage(
      appSlug: $appSlug
      search: $search
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        name
        url
        deploysPaused
        ingressPaused
        requiredApprovals
        registeredAppSlug
        clusterSlug
        clusterId
        clusterProviderPluginSlug
        domainZone
        createdAt
        kind
        region
        ownedByMe
        settings {
          id
          key
          value
        }
      }
      totalCount
      page
      pageSize
    }
  }
`;

export const LIST_DEPLOYMENTS = gql`
  query ListDeployments($appSlug: String, $environmentName: String, $limit: Int) {
    astroliftDeployments(appSlug: $appSlug, environmentName: $environmentName, limit: $limit) {
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
  }
`;

/**
 * Cursor-paginated companion to ``LIST_DEPLOYMENTS`` (#1230).
 *
 * Speaks the platform page envelope — ``{ items, nextCursor, totalCount }``
 * out, ``limit`` + ``after`` in — so ``useCursorTable`` walks the whole
 * result set on the server instead of the surface fetching a capped slice
 * and paginating it in the browser, which is how deployment 101 became
 * unreachable from ``/deployments``.
 *
 * Filters compose intersectionally and are passed as static controller
 * variables, so changing one restarts the walk at page one. ``statuses``
 * is the plural filter (the field also takes a single ``status``);
 * ``isPreview`` splits preview-environment deployments from the rest.
 * The field takes no sort argument, so its table declares no
 * ``sortVariable`` and no ``Column.sortKey``.
 *
 * The row selection is a spreadable fragment rather than a plain
 * template-literal constant because this file IS in the codegen document
 * set — ``graphql-tag-pluck`` cannot resolve a bare ``${FIELDS}``
 * interpolation and (with ``noSilentErrors``) would fail the run.
 * ``LIST_DEPLOYMENTS`` keeps its inline copy until its last consumer is
 * migrated, then goes away with it.
 */
const DEPLOYMENT_FIELDS = gql`
  fragment DeploymentFields on AstroliftDeployment {
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
`;

export const LIST_DEPLOYMENTS_PAGE = gql`
  ${DEPLOYMENT_FIELDS}
  query ListDeploymentsPage(
    $appSlug: String
    $environmentName: String
    $statuses: [String!]
    $isPreview: Boolean
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
      isPreview: $isPreview
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
    ) {
      items {
        ...DeploymentFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_DEPLOYMENT = gql`
  query GetDeployment($id: String!) {
    astroliftDeployment(id: $id) {
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
      phases {
        name
        startedAt
        completedAt
        failedAt
        healthyAt
      }
      startedAt
      succeededAt
      failedAt
      endedAt
      durationSeconds
      createdAt
      ciActorKind
      commitSha
      commitMessage
      commitAuthor
      branch
      ciRunUrl
      ciProvider
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
  }
`;

export const GET_DEPLOYMENT_RELEASE_NOTES = gql`
  query GetDeploymentReleaseNotes($deploymentId: String!) {
    astroliftDeploymentReleaseNotes(deploymentId: $deploymentId) {
      baseSha
      headSha
      compareUrl
      commits {
        sha
        subject
        author
        isMerge
      }
      pullRequests {
        number
        title
        body
        author
        mergedAt
        prUrl
      }
    }
  }
`;

export const GET_DEPLOYMENT_APPROVAL_HISTORY = gql`
  query GetDeploymentApprovalHistory($deploymentId: String!) {
    astroliftDeploymentApprovalHistory(deploymentId: $deploymentId) {
      id
      action
      decision
      actorKind
      actorId
      actorDisplay
      occurredAt
      reason
    }
  }
`;

export const GET_DEPLOYMENT_LOG = gql`
  query GetDeploymentLog($deploymentId: String!) {
    astroliftDeploymentLog(deploymentId: $deploymentId) {
      id
      deploymentId
      status
      message
      detail
      occurredAt
    }
  }
`;

export const GET_DEPLOYMENT_RUN_LOG_PAGE = gql`
  query GetDeploymentRunLogPage($deploymentId: String!, $cursor: String, $limit: Int = 100) {
    astroliftDeploymentRunLogPage(deploymentId: $deploymentId, cursor: $cursor, limit: $limit) {
      items {
        id
        deploymentId
        status
        phase
        event
        message
        detail
        occurredAt
      }
      nextCursor
      hasMore
      pageSize
    }
  }
`;

export const DOWNLOAD_DEPLOYMENT_RUN_LOG = gql`
  query DownloadDeploymentRunLog($deploymentId: String!) {
    astroliftDeploymentRunLogDownload(deploymentId: $deploymentId) {
      filename
      content
      contentType
    }
  }
`;

export const GET_DEPLOYMENT_METRICS = gql`
  query GetDeploymentMetrics($windowDays: Int = 30) {
    astroliftDeploymentMetrics(windowDays: $windowDays) {
      windowDays
      total
      succeeded
      failed
      rolledBack
      inFlight
      successRate
      meanDurationSeconds
      p95DurationSeconds
      dailySucceeded
      dailyFailed
      dailyMeanDurationSeconds
    }
  }
`;

export const LIST_SCHEDULED_JOB_RUNS = gql`
  query ListScheduledJobRuns($appSlug: String, $environmentName: String, $limit: Int) {
    astroliftScheduledJobRuns(appSlug: $appSlug, environmentName: $environmentName, limit: $limit) {
      id
      registeredAppSlug
      environmentName
      workloadSlug
      k8sJobName
      status
      startedAt
      endedAt
      durationSeconds
      exitCode
      logExcerpt
      output
      createdAt
    }
  }
`;

const SCHEDULED_JOB_RUN_FIELDS = gql`
  fragment ScheduledJobRunFields on AstroliftScheduledJobRun {
    id
    registeredAppSlug
    environmentName
    workloadSlug
    k8sJobName
    status
    startedAt
    endedAt
    durationSeconds
    exitCode
    logExcerpt
    output
    createdAt
  }
`;

/**
 * Cursor-paginated companion to ``LIST_SCHEDULED_JOB_RUNS`` (#1230).
 *
 * ``appSlug`` / ``environmentName`` stay optional — omitted, the field pages
 * every scheduled-job run in the org, which is what the fleet-wide Jobs
 * surface wants; passed, they scope to one app or one environment. There is
 * no sort argument, so a table over it declares no ``sortVariable`` and no
 * ``Column.sortKey``.
 *
 * ``$limit: Int`` is nullable against the schema's ``limit: Int! = 50``: the
 * argument's default is what makes that legal, and the controller always
 * sends a value.
 */
export const LIST_SCHEDULED_JOB_RUNS_PAGE = gql`
  ${SCHEDULED_JOB_RUN_FIELDS}
  query ListScheduledJobRunsPage(
    $appSlug: String
    $environmentName: String
    $workloadSlug: String
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftScheduledJobRunsFilter
  ) {
    astroliftScheduledJobRunsPage(
      appSlug: $appSlug
      environmentName: $environmentName
      workloadSlug: $workloadSlug
      search: $search
      limit: $limit
      after: $after
      filter: $filter
    ) {
      items {
        ...ScheduledJobRunFields
      }
      nextCursor
      totalCount
    }
  }
`;

// Single scheduled-job run by id — backs a cold detail deep-link (#1118)
// when the run has aged out of the LIST_SCHEDULED_JOB_RUNS window. Same
// field set so the normalized cache entry is complete either way.
export const GET_SCHEDULED_JOB_RUN = gql`
  query GetScheduledJobRun($id: String!) {
    astroliftScheduledJobRun(id: $id) {
      id
      registeredAppSlug
      environmentName
      workloadSlug
      k8sJobName
      status
      startedAt
      endedAt
      durationSeconds
      exitCode
      logExcerpt
      output
      createdAt
    }
  }
`;

export const LIST_COMMAND_RUNS = gql`
  query ListCommandRuns($appSlug: String, $limit: Int) {
    astroliftCommandRuns(appSlug: $appSlug, limit: $limit) {
      id
      registeredAppSlug
      workloadSlug
      invokedByUsername
      command
      startedAt
      endedAt
      exitCode
      logExcerpt
      output
      createdAt
    }
  }
`;

const COMMAND_RUN_FIELDS = gql`
  fragment CommandRunFields on AstroliftCommandRun {
    id
    registeredAppSlug
    workloadSlug
    invokedByUsername
    command
    startedAt
    endedAt
    exitCode
    logExcerpt
    output
    createdAt
  }
`;

/**
 * Cursor-paginated companion to ``LIST_COMMAND_RUNS`` (#1230). ``appSlug``
 * is the field's only filter besides ``search`` — there is no environment
 * axis on a one-off exec — and it takes no sort argument.
 */
export const LIST_COMMAND_RUNS_PAGE = gql`
  ${COMMAND_RUN_FIELDS}
  query ListCommandRunsPage(
    $appSlug: String
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftCommandRunsFilter
  ) {
    astroliftCommandRunsPage(
      appSlug: $appSlug
      search: $search
      limit: $limit
      after: $after
      filter: $filter
    ) {
      items {
        ...CommandRunFields
      }
      nextCursor
      totalCount
    }
  }
`;

// Single command (one-off exec) run by id — cold detail deep-link (#1118).
export const GET_COMMAND_RUN = gql`
  query GetCommandRun($id: String!) {
    astroliftCommandRun(id: $id) {
      id
      registeredAppSlug
      workloadSlug
      invokedByUsername
      command
      startedAt
      endedAt
      exitCode
      logExcerpt
      output
      createdAt
    }
  }
`;

export const LIST_APP_HEALTH_SUMMARY = gql`
  query ListAppHealthSummary {
    astroliftAppHealthSummary {
      appSlug
      appName
      primitiveKind
      environmentCount
      latestDeploymentStatus
      latestImageTag
      lastDeployedAt
      hasRecentFailure
    }
  }
`;

export const LIST_APP_DOMAINS = gql`
  query ListAppDomains($appSlug: String!) {
    astroliftAppDomains(appSlug: $appSlug) {
      id
      hostname
      certState
      validationMethod
      validationToken
      lastCheckedAt
      isActive
      registeredAppSlug
      createdAt
      txtChallengeToken
      expectedCnameTarget
      isPlatformManagedZone
      lastValidationError
      certificateState
      lastCertificateError
      byoCertificateUploadedAt
      certExpiresAt
      certIssuerSerial
      certObservabilityStatus
      requiredDnsRecords {
        kind
        name
        value
        ttl
        propagated
        lastCheckedAt
        message
      }
      redirectRules {
        id
        kind
        sourcePattern
        destinationUrl
        httpStatus
        preserveQueryString
        priority
      }
      pathRoutes {
        id
        pathPrefix
        targetWorkloadSlug
        targetPort
        stripPrefix
        priority
      }
      isWildcard
      sniCertRef
      edgeAuthState
    }
  }
`;

export const LIST_APP_DEPLOY_TOKENS = gql`
  query ListAppDeployTokens($appSlug: String!) {
    astroliftAppDeployTokens(appSlug: $appSlug) {
      id
      name
      last4
      scopes
      expiresAt
      lastUsedAt
      lastUsedIp
      lastUsedAgent
      isRevoked
      lastRotatedAt
      createdAt
    }
  }
`;

const DEPLOY_TOKEN_FIELDS = gql`
  fragment DeployTokenFields on AstroliftDeployToken {
    id
    name
    last4
    scopes
    expiresAt
    lastUsedAt
    lastUsedIp
    lastUsedAgent
    isRevoked
    lastRotatedAt
    createdAt
  }
`;

// Cursor-paginated companion to ``LIST_APP_DEPLOY_TOKENS`` (#1230).
// ``appSlug`` stays required — a deploy token only exists in the context
// of one app, so there is no all-apps page to fall back to.
export const LIST_APP_DEPLOY_TOKENS_PAGE = gql`
  ${DEPLOY_TOKEN_FIELDS}
  query ListAppDeployTokensPage($appSlug: String!, $search: String, $limit: Int, $after: String) {
    astroliftAppDeployTokensPage(appSlug: $appSlug, search: $search, limit: $limit, after: $after) {
      items {
        ...DeployTokenFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const LIST_PREVIEW_ENVIRONMENTS = gql`
  query ListPreviewEnvironments($appSlug: String) {
    astroliftPreviewEnvironments(appSlug: $appSlug) {
      id
      registeredAppSlug
      prNumber
      branch
      commitSha
      status
      hostname
      namespace
      lastDeployedAt
      tornDownAt
      ttlUntil
      sourceUrl
      prUrl
      isManual
      aggregateResources {
        cpuCores
        memoryBytes
        podCount
      }
      estimatedDailyCostUsd
      estimatedCostNotes
      estimatedCostApproximate
      estimatedCostNotes
      estimatedCostApproximate
    }
  }
`;

const PREVIEW_ENVIRONMENT_FIELDS = gql`
  fragment PreviewEnvironmentFields on AstroliftPreviewEnvironment {
    id
    version
    environmentStatus
    runtimeStatus
    environment {
      previewId
      previewVersion
      appId
      appVersion
      appSlug
      environmentId
      environmentVersion
      environmentName
      clusterId
      clusterVersion
      namespace
    }
    registeredAppSlug
    prNumber
    branch
    commitSha
    status
    hostname
    namespace
    lastDeployedAt
    tornDownAt
    ttlUntil
    sourceUrl
    prUrl
    isManual
    isPinned
    pinnedAt
    pinnedByEmail
    pinReason
    openedByLogin
    openedByUserId
    openedByMe
    failureReason
    aggregateResources {
      cpuCores
      memoryBytes
      podCount
    }
    estimatedDailyCostUsd
    estimatedCostNotes
    estimatedCostApproximate
  }
`;

/** Exact identity lookup: basic reads never query runtime pods or pricing. */
export const GET_PREVIEW_ENVIRONMENT = gql`
  ${PREVIEW_ENVIRONMENT_FIELDS}
  query GetPreviewEnvironment($id: GUID!, $includeRuntimeCost: Boolean! = false) {
    astroliftPreviewEnvironment(id: $id, includeRuntimeCost: $includeRuntimeCost) {
      ...PreviewEnvironmentFields
    }
  }
`;

export const GET_PREVIEW_LOGS = gql`
  query GetPreviewLogs(
    $appSlug: String!
    $previewId: GUID!
    $expectedEnvironmentId: GUID!
    $ifMatchPreviewVersion: Int!
    $ifMatchEnvironmentVersion: Int!
    $since: DateTime!
    $until: DateTime!
    $limit: Int! = 200
  ) {
    astroliftAppLogs(
      appSlug: $appSlug
      previewId: $previewId
      expectedEnvironmentId: $expectedEnvironmentId
      ifMatchPreviewVersion: $ifMatchPreviewVersion
      ifMatchEnvironmentVersion: $ifMatchEnvironmentVersion
      since: $since
      until: $until
      limit: $limit
    ) {
      reason
      historicalAvailable
      items {
        timestamp
        message
        level
        podName
        container
      }
    }
  }
`;

export const GET_PREVIEW_DEPLOYMENTS_PAGE = gql`
  query GetPreviewDeploymentsPage(
    $id: GUID!
    $expectedEnvironmentId: GUID!
    $ifMatchPreviewVersion: Int!
    $ifMatchEnvironmentVersion: Int!
    $limit: Int! = 20
    $after: String
  ) {
    astroliftPreviewDeploymentsPage(
      id: $id
      expectedEnvironmentId: $expectedEnvironmentId
      ifMatchPreviewVersion: $ifMatchPreviewVersion
      ifMatchEnvironmentVersion: $ifMatchEnvironmentVersion
      limit: $limit
      after: $after
    ) {
      nextCursor
      totalCount
      items {
        id
        version
        appId
        environmentId
        status
        triggerKind
        commitSha
        imageTag
        createdAt
        startedAt
        endedAt
      }
    }
  }
`;

/**
 * Cursor-paginated companion to ``LIST_PREVIEW_ENVIRONMENTS`` (#1230).
 *
 * ``appSlug`` stays optional so the same document serves both the app's
 * Previews tab and the fleet-wide Previews surface. ``filter`` (status,
 * openedBy, manual) and a one-key ``sort`` (created, deployed or ttl, either
 * direction; default ``-created``) arrived with #2155; both are optional.
 */
export const LIST_PREVIEW_ENVIRONMENTS_PAGE = gql`
  ${PREVIEW_ENVIRONMENT_FIELDS}
  query ListPreviewEnvironmentsPage(
    $appSlug: String
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftPreviewEnvironmentsFilter
    $sort: String
  ) {
    astroliftPreviewEnvironmentsPage(
      appSlug: $appSlug
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
    ) {
      items {
        ...PreviewEnvironmentFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const LIST_APP_PODS = gql`
  query ListAppPods($appSlug: String!, $environmentName: String) {
    astroliftAppPods(appSlug: $appSlug, environmentName: $environmentName) {
      name
      workload
      status
      phase
      ready
      restarts
      age
      node
      containerStatuses {
        name
        ready
        restarts
        image
        state
        waitingReason
        terminatedReason
        kind
        lastRestartReasons
        lastRestartAt
        resources {
          cpuRequest
          cpuLimit
          memoryRequest
          memoryLimit
        }
      }
      recentErrorEvent {
        reason
        message
        type
        count
        lastSeen
      }
    }
  }
`;

/**
 * Workload-detail pod-forensics surface (#429). Returns pods bucketed
 * by their rolled-up status with the expander payload (pod names +
 * ages + ready) the status grid renders inline. Worst-first
 * ordering happens server-side so every client agrees on the row
 * order without re-sorting.
 */
export const GET_WORKLOAD_POD_STATUS_BREAKDOWN = gql`
  query GetWorkloadPodStatusBreakdown(
    $appSlug: String!
    $workloadSlug: String!
    $environmentName: String
  ) {
    astroliftWorkloadPodStatusBreakdown(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      environmentName: $environmentName
    ) {
      status
      count
      percent
      pods {
        name
        age
        ready
      }
    }
  }
`;

// #377 — operator-facing observability cards on the app detail page.
// Each query backs one card; the resolvers degrade to empty / null
// when the cluster's plugin doesn't implement the read method, so
// the FE empty state covers both "not configured" + "not yet
// supported on this cloud" with the same UX.

export const LIST_APP_DNS_RECORDS = gql`
  query ListAppDnsRecords($appSlug: String!, $environmentName: String) {
    astroliftAppDnsRecords(appSlug: $appSlug, environmentName: $environmentName) {
      reason
      records {
        name
        type
        value
        ttl
        propagationStatus
      }
    }
  }
`;

export const LIST_APP_CERTIFICATES = gql`
  query ListAppCertificates($appSlug: String!, $environmentName: String) {
    astroliftAppCertificates(appSlug: $appSlug, environmentName: $environmentName) {
      reason
      certificates {
        id
        hostname
        issuer
        notAfter
        daysUntilExpiry
        renewalStatus
      }
    }
  }
`;

export const GET_APP_IDENTITY_BINDING = gql`
  query GetAppIdentityBinding($appSlug: String!, $environmentName: String) {
    astroliftAppIdentityBinding(appSlug: $appSlug, environmentName: $environmentName) {
      reason
      binding {
        kind
        roleArnOrPrincipal
        trustPolicySummary
        lastUsedAt
      }
    }
  }
`;

// #436 A — blast-radius preview for the deregister modal. Fired on
// modal-open so the operator audits actual object names (not generic
// resource-class labels) before typing the confirm token. Read-only
// against real platform state — safe to refetch on every open. The
// FE groups the response into a collapsible tree by destination.

export const PREVIEW_DEREGISTER_APP = gql`
  query PreviewDeregisterApp($appSlug: String!) {
    previewAstroliftDeregister(appSlug: $appSlug) {
      appSlug
      appName
      totalResourceCount
      registryRepoUri
      k8sObjects {
        clusterSlug
        namespace
        apiVersion
        kind
        name
      }
      managedServices {
        id
        name
        kind
        variant
        environmentName
        status
      }
      secretRefs {
        id
        bundleSlug
        environmentName
        clusterSlug
        prefix
      }
      deployTokens {
        id
        name
        last4
        environmentName
      }
      sourceWebhook {
        installed
        repo
        hookId
      }
      identityRoles {
        clusterSlug
        kind
        roleArnOrPrincipal
      }
    }
  }
`;

// #436 D — in-flight deployment preview for the force-redeploy CTA.
// Returns the exact ``Deployment`` rows the recovery path will transition
// to FAILED so the operator can audit before confirming. ``environmentName``
// is optional — omitting it widens the preview to every env on the app.

export const PREVIEW_FORCE_REDEPLOY = gql`
  query PreviewForceRedeploy($appSlug: String!, $environmentName: String) {
    previewAstroliftForceRedeploy(appSlug: $appSlug, environmentName: $environmentName) {
      appSlug
      environmentName
      inFlightDeployments {
        id
        environmentName
        workloadSlug
        status
        imageTag
        startedAt
        createdAt
        triggerKind
        triggeredByDisplay
        ciActorKind
        ciRunUrl
      }
    }
  }
`;

// --- Deploy-vs-deploy comparison (#652) ---
export const COMPARE_DEPLOYMENTS = gql`
  query CompareDeployments($idA: String!, $idB: String!) {
    astroliftCompareDeployments(idA: $idA, idB: $idB) {
      deploymentAId
      deploymentBId
      baseSha
      headSha
      compareUrl
      manifestDiff {
        op
        path
        before
        after
      }
      imageDiffSummary
    }
  }
`;

// --- User alert subscriptions (#703) ---
export const LIST_MY_ALERT_SUBSCRIPTIONS = gql`
  query ListMyAlertSubscriptions($appSlug: String) {
    astroliftMyAlertSubscriptions(appSlug: $appSlug) {
      id
      appSlug
      alertKind
      channel
      enabled
    }
  }
`;

// --- Task runs (#801) ---
// The backend GQL layer is not yet wired; this query is a stub so the
// FE can reference it with errorPolicy:"ignore" and degrade gracefully
// to EmptyState until the resolver lands.
export const LIST_TASK_RUNS = gql`
  query ListTaskRuns($appSlug: String, $limit: Int) {
    astroliftTaskRuns(appSlug: $appSlug, limit: $limit) {
      id
      registeredAppSlug
      workloadSlug
      triggerKind
      triggeredByUsername
      command
      status
      exitCode
      startedAt
      endedAt
      durationSeconds
      k8sJobName
      createdAt
    }
  }
`;

const TASK_RUN_FIELDS = gql`
  fragment TaskRunFields on AstroliftTaskRun {
    id
    registeredAppSlug
    workloadSlug
    triggerKind
    triggeredByUsername
    command
    status
    exitCode
    startedAt
    endedAt
    durationSeconds
    k8sJobName
    createdAt
  }
`;

/**
 * Cursor-paginated companion to ``LIST_TASK_RUNS`` (#1230).
 *
 * The page field filters on ``appSlug`` / ``workloadSlug`` / ``status`` as
 * well as ``search``; all four are static controller variables, so changing
 * one restarts the walk at page one. No sort argument, so no
 * ``sortVariable`` / ``Column.sortKey``.
 */
export const LIST_TASK_RUNS_PAGE = gql`
  ${TASK_RUN_FIELDS}
  query ListTaskRunsPage(
    $appSlug: String
    $workloadSlug: String
    $status: String
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftTaskRunsPage(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      status: $status
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ...TaskRunFields
      }
      nextCursor
      totalCount
    }
  }
`;

// Single task run by id — cold detail deep-link (#1118) for runs aged out
// of the LIST_TASK_RUNS window.
export const GET_TASK_RUN = gql`
  query GetTaskRun($id: String!) {
    astroliftTaskRun(id: $id) {
      id
      registeredAppSlug
      workloadSlug
      triggerKind
      triggeredByUsername
      command
      status
      exitCode
      startedAt
      endedAt
      durationSeconds
      k8sJobName
      createdAt
    }
  }
`;

const AGENT_RUN_FIELDS = gql`
  fragment AgentRunFields on AstroliftAgentRun {
    id
    registeredAppSlug
    workloadSlug
    triggerKind
    triggeredByUsername
    status
    input
    output
    reasoningTraceUrl
    toolCallsCount
    retryCount
    startedAt
    endedAt
    durationSeconds
    k8sPodName
    resultTtlHours
    createdAt
  }
`;

/**
 * Agent-run history (#1230). Unlike its neighbours this one has no
 * unpaginated sibling to migrate from — ``astroliftAgentRuns`` was never
 * given a query document — so the page field is the only way the FE reads
 * agent runs, and the selection is the whole ``AstroliftAgentRun`` type.
 *
 * Filters: ``appSlug`` / ``workloadSlug`` / ``projectSlug`` / ``status``
 * plus ``search``, all static controller variables. No sort argument.
 * ``input`` / ``output`` are JSON blobs carried per row on purpose — the run
 * drawer reads them off the row it was opened from, the same way the command
 * -run list carries its ``output`` excerpt.
 */
export const LIST_AGENT_RUNS_PAGE = gql`
  ${AGENT_RUN_FIELDS}
  query ListAgentRunsPage(
    $appSlug: String
    $workloadSlug: String
    $projectSlug: String
    $status: String
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftAgentRunsPage(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      projectSlug: $projectSlug
      status: $status
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ...AgentRunFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_APP_DEPLOYMENT_ACTIVITY = gql`
  query GetAppDeploymentActivity(
    $appSlug: String!
    $filter: AstroliftDeploymentsFilter
    $after: String
  ) {
    astroliftDeploymentsPage(appSlug: $appSlug, filter: $filter, limit: 100, after: $after) {
      items {
        id
        status
        createdAt
        startedAt
      }
      nextCursor
    }
  }
`;

/** APP_UPDATE-scoped, validated rotation window; never contains a secret. */
export const GET_APP_DEPLOY_TOKEN_ROTATION_METADATA = gql`
  query GetAppDeployTokenRotationMetadata($appSlug: String!) {
    astroliftAppDeployTokenRotationMetadata(appSlug: $appSlug) {
      rotationGraceSeconds
    }
  }
`;

export const GET_APP_EXEC_TARGET = gql`
  query GetAppExecTarget(
    $appSlug: String!
    $workloadSlug: String!
    $environmentId: GUID!
    $podName: String!
    $container: String!
  ) {
    astroliftAppExecTarget(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      environmentId: $environmentId
      podName: $podName
      container: $container
    ) {
      workloadId
      workloadVersion
      appId
      appVersion
      environmentId
      environmentName
      environmentVersion
      clusterId
      clusterVersion
      namespace
      podName
      podUid
      container
      podBinding
      resumable
    }
  }
`;
