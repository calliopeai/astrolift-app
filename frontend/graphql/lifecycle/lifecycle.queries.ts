import { gql } from "@apollo/client";

export const LIST_ENVIRONMENTS = gql`
  query ListEnvironments($appSlug: String) {
    astroliftEnvironments(appSlug: $appSlug) {
      id
      name
      url
      deploysPaused
      requiredApprovals
      registeredAppSlug
      clusterSlug
      domainZone
      createdAt
    }
  }
`;

export const LIST_DEPLOYMENTS = gql`
  query ListDeployments(
    $appSlug: String
    $environmentName: String
    $limit: Int
  ) {
    astroliftDeployments(
      appSlug: $appSlug
      environmentName: $environmentName
      limit: $limit
    ) {
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
      branch
      ciActorKind
      ciProvider
      ciRunUrl
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
      branch
      ciRunUrl
      ciProvider
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
  query ListScheduledJobRuns(
    $appSlug: String
    $environmentName: String
    $limit: Int
  ) {
    astroliftScheduledJobRuns(
      appSlug: $appSlug
      environmentName: $environmentName
      limit: $limit
    ) {
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
      }
    }
  }
`;
