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
      status
      imageTag
      imageDigest
      clusterRevision
      approvalsRequired
      approvalsReceived
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
      status
      imageTag
      imageDigest
      clusterRevision
      approvalsRequired
      approvalsReceived
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
      requiredDnsRecords {
        kind
        name
        value
        ttl
        propagated
        lastCheckedAt
        message
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
