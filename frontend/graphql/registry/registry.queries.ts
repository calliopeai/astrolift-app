import { gql } from "@apollo/client";

const APP_FIELDS = `
  id
  slug
  name
  description
  organizationSlug
  teamSlug
  projectSlug
  sourceKind
  sourceRepo
  sourceUrl
  manifestPath
  defaultBranch
  manifestHash
  registryRepoUri
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
  deployBranch
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
    }
  }
`;
