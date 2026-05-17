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
  ecrRepoUri
  ecrPushRoleArn
  k8sNamespace
  subdomain
  isActive
  provisioningStatus
  provisioningError
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
  createdAt
  updatedAt
  deletedAt
`;

export const LIST_APPS = gql`
  query ListApps {
    astroliftApps {
      ${APP_FIELDS}
    }
  }
`;

export const GET_APP = gql`
  query GetApp($slug: String!) {
    astroliftApp(slug: $slug) {
      ${APP_FIELDS}
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
      registeredAppSlug
    }
  }
`;

export const GET_RENDERED_MANIFEST = gql`
  query GetRenderedManifest(
    $appSlug: String!
    $environmentName: String
    $imageTag: String
  ) {
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
