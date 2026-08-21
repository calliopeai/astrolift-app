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
      expiresAt
      setVia
      scope
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
      keyNames
      lastKnownKeysAt
    }
  }
`;

export const LIST_PROJECT_RESOURCES = gql`
  query ListProjectResources($projectId: GUID!) {
    astroliftProjectResourceClusters(projectId: $projectId) {
      id
      slug
      name
      providerPluginSlug
      region
      isActive
      lifecycle
    }
    astroliftProjectManagedServices(projectId: $projectId) {
      id
      name
      kind
      variant
      status
      statusError
      config
      appliedConfig
      operationKind
      operationWorkflowId
      operationRunId
      operationStartedAt
      operationCompletedAt
      projectSlug
      ownerScope
      clusterSlug
      environmentName
      createdAt
      updatedAt
      lastActionAt
      lastActionKind
      editableFields
      attachments {
        id
        consumerKind
        consumerSlug
        environmentName
      }
      volumeBindings {
        id
        name
        mountPath
        subPath
        sourceKind
        protocol
        claimName
        claimNamespace
        storageClassName
        csiDriver
        readOnly
        capacity
        accessModes
        workloadNames
        containerNames
        credentialReferenceCount
      }
    }
    astroliftProjectSecretBundles(projectId: $projectId) {
      id
      slug
      name
      backendRef
      projectSlug
      clusterSlug
      keyCount
      keyNames
      lastKnownKeysAt
      createdAt
      consumers {
        id
        consumerKind
        consumerSlug
        environmentName
      }
    }
  }
`;

export const LIST_PROJECT_MANAGED_SERVICE_CATALOG = gql`
  query ListProjectManagedServiceCatalog($projectId: GUID!, $clusterId: GUID!) {
    astroliftProjectManagedServiceCatalog(projectId: $projectId, clusterId: $clusterId) {
      id
      providerPluginSlug
      kind
      variant
      displayName
      description
      status
      available
      unavailableReason
      isDefaultForKind
      sizeOptions
      configSchema
      bindingEnvs
      issueUrl
    }
  }
`;

const MANAGED_SERVICE_FIELDS = gql`
  fragment ManagedServiceFields on AstroliftManagedService {
    id
    kind
    name
    variant
    environmentName
    registeredAppSlug
    status
    statusError
    config
    appliedConfig
    operationKind
    operationWorkflowId
    operationRunId
    operationStartedAt
    operationCompletedAt
    createdAt
    updatedAt
    lastActionAt
    lastActionKind
    editableFields
    volumeBindings {
      id
      name
      mountPath
      subPath
      sourceKind
      protocol
      claimName
      claimNamespace
      storageClassName
      csiDriver
      readOnly
      capacity
      accessModes
      workloadNames
      containerNames
      credentialReferenceCount
    }
  }
`;

export const LIST_MANAGED_SERVICES = gql`
  ${MANAGED_SERVICE_FIELDS}
  query ListManagedServices($appSlug: String!, $environmentName: String) {
    astroliftManagedServices(appSlug: $appSlug, environmentName: $environmentName) {
      ...ManagedServiceFields
    }
  }
`;

/**
 * Cursor-paginated companion to ``LIST_MANAGED_SERVICES`` (#1230). The flat
 * field is deprecated for applying no ordering at all — row order was
 * whatever Postgres returned — so this is also what makes the list stable
 * between renders.
 */
export const LIST_MANAGED_SERVICES_PAGE = gql`
  ${MANAGED_SERVICE_FIELDS}
  query ListManagedServicesPage(
    $appSlug: String!
    $environmentName: String
    $search: String
    $limit: Int
    $after: String
  ) {
    astroliftManagedServicesPage(
      appSlug: $appSlug
      environmentName: $environmentName
      search: $search
      limit: $limit
      after: $after
    ) {
      items {
        ...ManagedServiceFields
      }
      nextCursor
      totalCount
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

// Email observability surface (#629, #631, #632, #633, #634) -----------

export const GET_EMAIL_SERVICE_DETAIL = gql`
  query GetEmailServiceDetail($managedServiceId: GUID!) {
    astroliftEmailServiceDetail(managedServiceId: $managedServiceId) {
      managedServiceId
      pluginSlug
      region
      identity
      quota {
        maxSendRate
        max24HourSend
        sentLast24h
      }
      accountStatus {
        sendingEnabled
        productionAccess
        reputationScore
        bounceRatePct
        complaintRatePct
      }
      identityVerification {
        identity
        isDomain
        status
        verificationToken
        dkimTokens {
          token
          cnameHost
          cnameTarget
        }
      }
      dnsAuthStatus {
        identity
        checkedAt
        overall
        dkim {
          protocol
          outcome
          records
          message
        }
        spf {
          protocol
          outcome
          records
          message
        }
        dmarc {
          protocol
          outcome
          records
          message
        }
      }
      suppressionEntries {
        address
        reason
        suppressedAt
        detail
      }
      unsupportedNotes
    }
  }
`;

// Email templates + message log + engagement (#624, #625, #626, #628, #635)

export const GET_EMAIL_TEMPLATES = gql`
  query GetEmailTemplates($managedServiceId: GUID!) {
    astroliftEmailTemplates(managedServiceId: $managedServiceId) {
      name
      subject
      htmlBody
      textBody
      createdAt
    }
  }
`;

export const GET_EMAIL_TEMPLATE_STATS = gql`
  query GetEmailTemplateStats($managedServiceId: GUID!, $name: String!, $days: Int) {
    astroliftEmailTemplateStats(managedServiceId: $managedServiceId, name: $name, days: $days) {
      timestamp
      sends
      deliveries
      bounces
      complaints
    }
  }
`;

export const GET_EMAIL_MESSAGES = gql`
  query GetEmailMessages(
    $managedServiceId: GUID!
    $limit: Int
    $eventKind: String
    $recipient: String
  ) {
    astroliftEmailMessages(
      managedServiceId: $managedServiceId
      limit: $limit
      eventKind: $eventKind
      recipient: $recipient
    ) {
      id
      messageId
      recipient
      subject
      eventKind
      occurredAt
      metadata
    }
  }
`;

export const GET_EMAIL_ENGAGEMENT_METRICS = gql`
  query GetEmailEngagementMetrics($managedServiceId: GUID!, $days: Int) {
    astroliftEmailEngagementMetrics(managedServiceId: $managedServiceId, days: $days) {
      totalSends
      totalDeliveries
      totalBounces
      totalComplaints
      totalOpens
      totalClicks
      bounceRatePct
      complaintRatePct
      openRatePct
      clickRatePct
      windowDays
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

/**
 * Per-key audit timeline for an app secret (#725). Reads the
 * MutationAuditLog filtered by operation in {setAppSecret,
 * deleteAppSecret, rotateAppSecret} and variables__input
 * matching the app + key. Newest first, capped at 50.
 *
 * Powers the History popover on revealed-secret rows in the
 * Secrets tab (#714 FE).
 */
export const GET_APP_SECRET_HISTORY = gql`
  query GetAppSecretHistory($appSlug: String!, $key: String!) {
    astroliftAppSecretHistory(appSlug: $appSlug, key: $key) {
      timestamp
      action
      success
      errorCode
      sourceIp
      actor {
        id
        username
      }
    }
  }
`;
