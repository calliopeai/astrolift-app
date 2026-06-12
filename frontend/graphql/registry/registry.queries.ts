import { gql } from "@apollo/client";

const APP_FIELDS = `
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
  lastSyncedHash
  manifestSyncState
  lastResyncAt
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
`;

/**
 * Per-row deployment-freshness fragment surfaced on the apps list
 * when ``includeFreshness: true`` (#405). The backend leaves these
 * fields null when the flag is off so the cheap list query stays
 * cheap; the FE opts in only on screens that render the health
 * pulse + last-deployed badge.
 */
const APP_FRESHNESS_FIELDS = `
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
`;

export const LIST_APPS = gql`
  query ListApps($includeFreshness: Boolean = false) {
    astroliftApps(includeFreshness: $includeFreshness) {
      ${APP_FIELDS}
      ${APP_FRESHNESS_FIELDS}
    }
  }
`;

/**
 * Server-side filter + cursor-pagination companion to ``LIST_APPS`` (#481).
 *
 * Returns a page envelope so the FE can render "N of M" + a "Load more"
 * cursor walk instead of pulling the entire registry into memory and
 * client-filtering. Filter axes match the backend resolver: ``search``
 * (case-insensitive contains on name/slug/description/repo), ``status``
 * (health-pulse bucket), ``teamSlug`` / ``projectSlug`` (exact match),
 * ``sourceKind`` (source-host enum). All filters compose intersectionally.
 *
 * ``cursor``/``limit`` follow the same shape ``astroliftEventsPage`` uses
 * (base64-JSON of ``(createdAt, guid)``); the FE treats the value as
 * opaque and just round-trips ``nextCursor`` back via ``fetchMore``.
 */
export const LIST_APPS_PAGE = gql`
  query ListAppsPage(
    $includeFreshness: Boolean = false
    $search: String
    $teamSlug: String
    $projectSlug: String
    $status: AstroliftAppListStatusFilter
    $sourceKind: AstroliftAppSourceKindFilter
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
      cursor: $cursor
      limit: $limit
    ) {
      items {
        ${APP_FIELDS}
        ${APP_FRESHNESS_FIELDS}
      }
      nextCursor
      totalCount
    }
  }
`;

export const GET_APP = gql`
  query GetApp($slug: String!, $includeDrift: Boolean = false) {
    astroliftApp(slug: $slug, includeDrift: $includeDrift) {
      ${APP_FIELDS}
      configDrift {
        hasDrift
        fields
        environmentName
        lastChecked
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
