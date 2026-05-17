import { gql } from "@apollo/client";

export const LIST_APP_SECRETS = gql`
  query ListAppSecrets($appSlug: String!, $environmentName: String) {
    astroliftAppSecrets(appSlug: $appSlug, environmentName: $environmentName) {
      id
      key
      environmentName
      source
      bundleSlug
      managedServiceKind
      isMasked
      lastEditedAt
      lastEditedBy {
        id
        username
        displayName
      }
    }
  }
`;

/**
 * Minimal version-only fetch on the parent app row (#497).
 *
 * The secrets editor needs the app's current ``version`` to pass on
 * ``setAppSecret(input.ifMatchVersion)`` so concurrent edits to other
 * settings (deploy strategy, security policy, …) can't race the
 * secret write. The full ``GET_APP`` query pulls dozens of fields we
 * don't need on this page, so we fetch only ``{id, version}`` and let
 * Apollo's normalized cache cross-pollinate with whatever else is in
 * memory.
 */
export const GET_APP_VERSION = gql`
  query GetAppVersion($slug: String!) {
    astroliftApp(slug: $slug) {
      id
      version
    }
  }
`;

export const LIST_APP_SECRET_BUNDLE_ATTACHMENTS = gql`
  query ListAppSecretBundleAttachments($appSlug: String!, $environmentName: String) {
    astroliftAppSecretBundleAttachments(appSlug: $appSlug, environmentName: $environmentName) {
      id
      registeredAppSlug
      environmentName
      bundleSlug
      bundleName
      prefix
      teamSlug
      keyCount
      mergeOrder
      attachedAt
    }
  }
`;

export const LIST_SECRET_BUNDLES = gql`
  query ListSecretBundles {
    astroliftSecretBundles {
      id
      slug
      name
      backendRef
      createdAt
      keyCount
      lastKnownKeysAt
    }
  }
`;

export const LIST_MANAGED_SERVICES = gql`
  query ListManagedServices($appSlug: String!, $environmentName: String) {
    astroliftManagedServices(appSlug: $appSlug, environmentName: $environmentName) {
      id
      kind
      name
      variant
      environmentName
      registeredAppSlug
      status
      statusError
      config
      createdAt
      updatedAt
      lastActionAt
      lastActionKind
    }
  }
`;

export const LIST_MANAGED_SERVICE_OBJECTS = gql`
  query ListManagedServiceObjects($managedServiceId: GUID!, $limit: Int) {
    astroliftManagedServiceObjects(managedServiceId: $managedServiceId, limit: $limit) {
      managedServiceId
      kind
      name
      truncated
      cacheAgeSeconds
      objects {
        key
        sizeBytes
        lastModified
      }
    }
  }
`;

export const GET_MANAGED_SERVICE_QUEUE_DEPTH = gql`
  query GetManagedServiceQueueDepth($managedServiceId: GUID!) {
    astroliftManagedServiceQueueDepth(managedServiceId: $managedServiceId) {
      managedServiceId
      kind
      name
      depth
      inFlight
      sampledAt
    }
  }
`;

// #488 Secret-change approval workflow ---------------------------------

const SECRET_CHANGE_PROPOSAL_FIELDS = gql`
  fragment SecretChangeProposalFields on AstroliftSecretChangeProposal {
    id
    registeredAppSlug
    environmentName
    op
    status
    proposerUserId
    proposerDisplayName
    payload
    payloadDiff
    requiredApproverCount
    approvalsCount
    expiresAt
    decidedAt
    appliedAt
    applyError
    createdAt
    approvals {
      id
      approverUserId
      approverDisplayName
      decision
      decidedAt
      reason
    }
  }
`;

export const LIST_SECRET_CHANGE_PROPOSALS = gql`
  ${SECRET_CHANGE_PROPOSAL_FIELDS}
  query ListSecretChangeProposals($appSlug: String, $status: String) {
    astroliftSecretChangeProposals(appSlug: $appSlug, status: $status) {
      ...SecretChangeProposalFields
    }
  }
`;

export const GET_SECRET_CHANGE_PROPOSAL = gql`
  ${SECRET_CHANGE_PROPOSAL_FIELDS}
  query GetSecretChangeProposal($id: GUID!) {
    astroliftSecretChangeProposal(id: $id) {
      ...SecretChangeProposalFields
    }
  }
`;
