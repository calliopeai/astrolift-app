import { gql } from "@apollo/client";

/**
 * Shared app row selection.
 *
 * A named gql fragment, not a plain template-literal constant:
 * ``graphql-tag-pluck`` (the extractor codegen runs over this directory)
 * cannot resolve a bare ``${FIELDS}`` interpolation, so a file that used
 * one had every operation in it silently dropped from
 * ``__generated__/operations.ts`` and had to be excluded from the document
 * set (#934). Spreading a real fragment is what puts this file back in.
 */
const APP_FIELDS = gql`
  fragment AppFields on AstroliftRegisteredApp {
    id
    slug
    name
    description
    organizationSlug
    teamSlug
    teamId
    teamName
    projectSlug
    projectId
    projectName
    sourceKind
    sourceRepo
    sourceUrl
    manifestPath
    defaultBranch
    manifestHash
    registryRepoUri
    reprovision {
      needsReprovision
      state
      reason
      elapsedSeconds
    }
    ecrRepoUri
    ecrPushRoleArn
    providerPluginSlug
    k8sNamespace
    subdomain
    managedHostname
    isActive
    provisioningStatus
    provisioningError
    provisioningProgress {
      currentStep
      completed
      totalSteps
    }
    deployTokenLast4
    logRetentionDays
    previewMaxActive
    previewEnabled
    triggerMode
    cronExpression
    deployBranch
    previewScreenshotUrl
    rawManifest
    rawManifestStaged
    rawManifestStagedHash
    lastSyncedHash
    manifestSyncState
    lastResyncAt
    manifestBootstrapStatus
    manifestBootstrapError
    sourceWebhookInstalledAt
    isArchived
    archivedAt
    webhookDeploysPaused
    webhookDeploysPausedAt
    webhookDeploysPausedByEmail
    webhookDeploysPauseReason
    activePreviewCount
    securityPolicy {
      blockOnCriticalCves
      blockOnMissingSignature
      blockOnHighCveThreshold
    }
    createdAt
    updatedAt
    deletedAt
    version
  }
`;

/**
 * Per-row deployment-freshness fragment surfaced on the apps list
 * when ``includeFreshness: true`` (#405). The backend leaves these
 * fields null when the flag is off so the cheap list query stays
 * cheap; the FE opts in only on screens that render the health
 * pulse + last-deployed badge.
 */
const APP_FRESHNESS_FIELDS = gql`
  fragment AppFreshnessFields on AstroliftRegisteredApp {
    lastDeployedAt
    healthPulse {
      status
      ageSeconds
      message
    }
    latestDeployment {
      id
      status
      startedAt
      endedAt
      createdAt
      environmentName
      triggeredBy
      imageTag
      commitSha
    }
  }
`;

export const LIST_APPS = gql`
  ${APP_FIELDS}
  ${APP_FRESHNESS_FIELDS}
  query ListApps($includeFreshness: Boolean = false) {
    astroliftApps(includeFreshness: $includeFreshness) {
      ...AppFields
      ...AppFreshnessFields
    }
  }
`;

/**
 * Server-side filter + cursor-pagination companion to ``LIST_APPS`` (#481).
 *
 * Returns a page envelope so the FE can render "N of M" + a prev/next
 * cursor walk instead of pulling the entire registry into memory and
 * client-filtering. Filter axes match the backend resolver: ``search``
 * (case-insensitive contains on name/slug/description/repo), ``status``
 * (health-pulse bucket), ``teamSlug`` / ``projectSlug`` (exact match),
 * ``sourceKind`` (source-host enum). All filters compose intersectionally.
 *
 * ``sortBy`` picks the server-side order (#729) — CREATED_DESC (the
 * default), DEPLOYED_DESC, or NAME_ASC. The cursor is keyed to the
 * active sort, so changing it restarts the walk at page one.
 *
 * ``cursor``/``limit`` follow the same shape ``astroliftEventsPage`` uses
 * (base64-JSON of ``(createdAt, guid)``); the FE treats the value as
 * opaque and just round-trips ``nextCursor`` back through the cursor
 * argument.
 */
export const LIST_APPS_PAGE = gql`
  ${APP_FIELDS}
  ${APP_FRESHNESS_FIELDS}
  query ListAppsPage(
    $includeFreshness: Boolean = false
    $search: String
    $teamSlug: String
    $projectSlug: String
    $status: AstroliftAppListStatusFilter
    $sourceKind: AstroliftAppSourceKindFilter
    $sortBy: AppsListSortKey = CREATED_DESC
    $cursor: String
    $limit: Int = 50
  ) {
    astroliftAppsPage(
      includeFreshness: $includeFreshness
      search: $search
      teamSlug: $teamSlug
      projectSlug: $projectSlug
      status: $status
      sourceKind: $sourceKind
      sortBy: $sortBy
      cursor: $cursor
      limit: $limit
    ) {
      items {
        ...AppFields
        ...AppFreshnessFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_APP = gql`
  ${APP_FIELDS}
  query GetApp($slug: String!, $includeDrift: Boolean = false) {
    astroliftApp(slug: $slug, includeDrift: $includeDrift) {
      ...AppFields
      stagedEnvChanges
      configDrift {
        hasDrift
        fields
        environmentName
        lastChecked
      }
      autowire {
        connected
        ciWorkflow
        webhook
        secrets
        checkedAt
        detail
      }
      ciWorkflowSyncStatus {
        state
        syncedTemplateVersion
        currentTemplateVersion
        syncedAt
        checkedAt
        path
        prUrl
        detail
        repoText
        renderedText
        repoTextPulledAt
      }
      settingsLastModified {
        deployStrategy
        deployTokens
        secrets
        managedServices
        domains
        webhooks
        members
        observability
      }
      retentionPolicies {
        id
        signal
        retentionDays
      }
    }
  }
`;

export const LIST_WORKLOADS = gql`
  query ListWorkloads($appSlug: String) {
    astroliftWorkloads(appSlug: $appSlug) {
      id
      slug
      name
      kind
      isPublic
      schedule
      concurrencyPolicy
      replicas
      cpuRequest
      cpuLimit
      memoryRequest
      memoryLimit
      hpaMinReplicas
      hpaMaxReplicas
      hpaTargetCpuPct
      storageClass
      storageSize
      volumes
      registeredAppSlug
    }
  }
`;

/**
 * Cursor-paginated companion to ``LIST_WORKLOADS`` (#1230).
 *
 * ``appSlug`` stays optional — omitted, the field pages every workload in
 * the org, which is what the fleet-wide surfaces want; passed, it scopes
 * to one app. ``search`` is the only other filter and there is no sort
 * argument, so a table over it declares no ``sortVariable`` and no
 * ``Column.sortKey``.
 *
 * ``$limit: Int`` is nullable against the schema's ``limit: Int! = 50``:
 * the argument's default is what makes that legal, and the controller
 * always sends a value.
 */
const WORKLOAD_FIELDS = gql`
  fragment WorkloadFields on AstroliftWorkload {
    id
    slug
    name
    kind
    isPublic
    schedule
    concurrencyPolicy
    replicas
    cpuRequest
    cpuLimit
    memoryRequest
    memoryLimit
    hpaMinReplicas
    hpaMaxReplicas
    hpaTargetCpuPct
    storageClass
    storageSize
    volumes
    registeredAppSlug
  }
`;

export const LIST_WORKLOADS_PAGE = gql`
  ${WORKLOAD_FIELDS}
  query ListWorkloadsPage($appSlug: String, $search: String, $limit: Int, $after: String) {
    astroliftWorkloadsPage(appSlug: $appSlug, search: $search, limit: $limit, after: $after) {
      items {
        ...WorkloadFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_RENDERED_MANIFEST = gql`
  query GetRenderedManifest($appSlug: String!, $environmentName: String, $imageTag: String) {
    astroliftRenderedManifest(
      appSlug: $appSlug
      environmentName: $environmentName
      imageTag: $imageTag
    ) {
      appSlug
      environmentName
      imageTag
      namespace
      resources
      error
      errorPath
      errorLine
      errorColumn
    }
  }
`;

export const GET_WORKLOAD = gql`
  query GetWorkload($appSlug: String!, $slug: String!) {
    astroliftWorkload(appSlug: $appSlug, slug: $slug) {
      id
      name
      slug
      kind
      isPublic
      schedule
      concurrencyPolicy
      replicas
      cpuRequest
      cpuLimit
      memoryRequest
      memoryLimit
      hpaMinReplicas
      hpaMaxReplicas
      hpaTargetCpuPct
      storageClass
      storageSize
      volumes
      registeredAppSlug
      inClusterServiceFqdn
    }
  }
`;

export const LIST_CONTAINERS = gql`
  query ListContainers($workloadSlug: String) {
    astroliftContainers(workloadSlug: $workloadSlug) {
      id
      name
      isPrimary
      imageRef
      dockerfilePath
      buildContext
      port
      command
      args
      env
      healthcheckKind
      healthcheckValue
      healthcheckPort
      startupProbe
      readinessProbe
      livenessProbe
      workloadSlug
    }
  }
`;

export const GET_PLATFORM_API_URL = gql`
  query GetPlatformApiUrl {
    astroliftPlatformApiUrl
  }
`;

/**
 * Projects in the current tenant org the viewer can assign apps to (#391).
 * Self-service: gated by the viewer's RoleBindings (ORG / TEAM /
 * PROJECT scope). Powers the project picker on the Settings
 * "Assign project" card; the FE groups the result by team.
 */
export const LIST_ASSIGNABLE_PROJECTS = gql`
  query ListAssignableAstroliftProjects {
    assignableAstroliftProjects {
      id
      slug
      name
      team {
        id
        slug
        name
      }
    }
  }
`;

export const LIST_APP_TEAM_ACCESSES = gql`
  query ListAppTeamAccesses($appSlug: String!) {
    astroliftAppTeamAccesses(appSlug: $appSlug) {
      id
      appId
      appSlug
      teamId
      teamSlug
      teamName
      accessLevel
      isHome
      createdAt
      updatedAt
    }
  }
`;

/**
 * Cursor-paginated companion to ``LIST_APP_TEAM_ACCESSES`` (#1230).
 *
 * ``appSlug`` stays required — a team grant only exists in the context of
 * one app, so there is no all-apps page to fall back to. ``search`` is the
 * only filter; the field takes no sort argument.
 */
const APP_TEAM_ACCESS_FIELDS = gql`
  fragment AppTeamAccessFields on AstroliftAppTeamAccess {
    id
    appId
    appSlug
    teamId
    teamSlug
    teamName
    accessLevel
    isHome
    createdAt
    updatedAt
  }
`;

export const LIST_APP_TEAM_ACCESSES_PAGE = gql`
  ${APP_TEAM_ACCESS_FIELDS}
  query ListAppTeamAccessesPage($appSlug: String!, $search: String, $limit: Int, $after: String) {
    astroliftAppTeamAccessesPage(appSlug: $appSlug, search: $search, limit: $limit, after: $after) {
      items {
        ...AppTeamAccessFields
      }
      nextCursor
      totalCount
    }
  }
`;

/**
 * Live workload scaling status (#430).
 *
 * Combines the manifest-side HPA configuration with the live
 * Deployment status read from the cluster driver. Powers the
 * scaling card on the workload-detail page.
 */
export const GET_WORKLOAD_SCALING_STATUS = gql`
  query GetWorkloadScalingStatus(
    $appSlug: String!
    $workloadSlug: String!
    $environmentName: String
  ) {
    astroliftWorkloadScalingStatus(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      environmentName: $environmentName
    ) {
      hpaEnabled
      hpaMinReplicas
      hpaMaxReplicas
      hpaTargetCpuPct
      currentReplicas
      desiredReplicas
      isScaling
      replicaLowerBound
      replicaUpperBound
      sourcedAt
    }
  }
`;

/**
 * Per-workload manifest preview + diff (#430).
 *
 * Returns the platform-rendered K8s resources scoped to one workload
 * plus, when a prior deployment exists for the same env, the resources
 * rendered at that deployment's image tag — so the FE can render an
 * image-tag diff client-side without a second round-trip.
 */
export const GET_WORKLOAD_MANIFEST = gql`
  query GetWorkloadManifest(
    $appSlug: String!
    $workloadSlug: String!
    $environmentName: String
    $imageTag: String
  ) {
    astroliftWorkloadManifest(
      appSlug: $appSlug
      workloadSlug: $workloadSlug
      environmentName: $environmentName
      imageTag: $imageTag
    ) {
      appSlug
      workloadSlug
      environmentName
      imageTag
      namespace
      resources
      previousImageTag
      previousDeploymentId
      resourcesPrevious
      error
      errorPath
      errorLine
      errorColumn
    }
  }
`;

// The doctor is a separate, on-demand query rather than a field on GET_APP:
// its checks resolve hostnames, read a push-role trust policy, and run the
// idempotent manifest resync, so folding it into the page query would make
// every app page load do live network probes.
export const GET_APP_DOCTOR = gql`
  query GetAppDoctor($appSlug: String!) {
    astroliftAppDoctor(appSlug: $appSlug) {
      healthy
      checks {
        key
        status
        detail
        fix
      }
    }
  }
`;
