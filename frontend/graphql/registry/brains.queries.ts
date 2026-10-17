import { gql } from "@apollo/client";

/**
 * Hosted semantic layers (brain apps) the viewer can read (#2320): the same
 * visibility, team shares and token ceiling as apps.
 */
export const LIST_BRAINS = gql`
  query ListBrains($search: String) {
    astroliftBrains(search: $search) {
      id
      slug
      name
      kind
      team
      project
      environments {
        name
        url
      }
      sharedWith {
        team
        access
      }
    }
  }
`;

/**
 * The selections the `astro brain` client reads (#2424), shared by the web
 * screens: a layer, its Mimwell substrate and a creation's progress.
 */
export const BRAIN_FIELDS = gql`
  fragment BrainFields on AstroliftBrain {
    id
    slug
    name
    kind
    team
    project
    environments {
      name
      url
    }
    sharedWith {
      team
      access
    }
  }
`;

export const BRAIN_SUBSTRATE_FIELDS = gql`
  fragment BrainSubstrateFields on AstroliftBrainSubstrate {
    authority
    blobs
    vectors
    graph
    cell
    persistence
    replicas
    image
    adopted
    adoptedStore
    graduationNeeded
    resources {
      kind
      name
      environment
      state
      detail
    }
  }
`;

export const BRAIN_STATUS_FIELDS = gql`
  ${BRAIN_SUBSTRATE_FIELDS}
  fragment BrainStatusFields on AstroliftBrainStatus {
    slug
    environmentName
    url
    served
    deployedCommit
    behind
    compiledAt
    warnings
    deployment {
      id
      status
      imageTag
      commitSha
      createdAt
    }
    sourceHead {
      branch
      commitSha
      committedAt
    }
    lastIngest {
      status
      conclusion
      url
      createdAt
    }
    substrate {
      ...BrainSubstrateFields
    }
  }
`;

export const BRAIN_CREATION_FIELDS = gql`
  ${BRAIN_FIELDS}
  fragment BrainCreationFields on AstroliftBrainCreation {
    id
    slug
    state
    repo
    templateRef
    commitSha
    deploymentId
    app {
      ...BrainFields
    }
    error {
      code
      message
      field
    }
  }
`;

/**
 * One layer the viewer can read (#2424), with the apps hosted on it and the
 * substrate last applied (no host reads; adoption and graduation are null
 * here). Null when it is not visible, archived or not a brain.
 */
export const GET_BRAIN = gql`
  ${BRAIN_FIELDS}
  ${BRAIN_SUBSTRATE_FIELDS}
  query GetBrain($slug: String!) {
    astroliftBrain(slug: $slug) {
      ...BrainFields
      hostedApps {
        slug
        environment
      }
      substrate {
        ...BrainSubstrateFields
      }
    }
  }
`;

/**
 * How fresh a layer is in one environment (#2424): its latest deployment, the
 * running commit against the branch head, the compile and the last ingest,
 * and the substrate with what the brain has adopted. Reads the source host,
 * so failed host reads come back in `warnings`.
 */
export const GET_BRAIN_STATUS = gql`
  ${BRAIN_STATUS_FIELDS}
  query GetBrainStatus($slug: String!, $environmentName: String! = "production") {
    astroliftBrainStatus(slug: $slug, environmentName: $environmentName) {
      ...BrainStatusFields
    }
  }
`;

/** The progress of one createBrain request (#2424), polled until READY or FAILED. */
export const GET_BRAIN_CREATION = gql`
  ${BRAIN_CREATION_FIELDS}
  query GetBrainCreation($id: GUID!) {
    astroliftBrainCreation(id: $id) {
      ...BrainCreationFields
    }
  }
`;

/**
 * The app behind a layer slug that no longer reads as a layer: an archived
 * brain stays an app (`astroliftApp` returns archived apps), and restoring
 * it is `restoreApp` on that app.
 */
export const GET_BRAIN_ARCHIVE_STATE = gql`
  query GetBrainArchiveState($slug: String!) {
    astroliftApp(slug: $slug) {
      id
      slug
      name
      appKind
      isArchived
      archivedAt
    }
  }
`;
