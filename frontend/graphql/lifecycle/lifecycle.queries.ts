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
      branch
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

export const LIST_APP_HEALTH_SUMMARY = gql`
  query ListAppHealthSummary {
    astroliftAppHealthSummary {
      appSlug
      appName
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
      aggregateResources {
        cpuCores
        memoryBytes
        podCount
      }
      estimatedDailyCostUsd
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
      name
      type
      value
      ttl
      propagationStatus
    }
  }
`;

export const LIST_APP_CERTIFICATES = gql`
  query ListAppCertificates($appSlug: String!, $environmentName: String) {
    astroliftAppCertificates(appSlug: $appSlug, environmentName: $environmentName) {
      id
      hostname
      issuer
      notAfter
      daysUntilExpiry
      renewalStatus
    }
  }
`;

export const GET_APP_IDENTITY_BINDING = gql`
  query GetAppIdentityBinding($appSlug: String!, $environmentName: String) {
    astroliftAppIdentityBinding(appSlug: $appSlug, environmentName: $environmentName) {
      kind
      roleArnOrPrincipal
      trustPolicySummary
      lastUsedAt
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
