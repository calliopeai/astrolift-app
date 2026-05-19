export type ListQuotasQueryVariables = Exact<{ [key: string]: never; }>;


export type ListQuotasQuery = { astroliftQuotas: Array<{ id: string, scopeKind: string, scopeId: string, resource: string, hardLimit: number, softLimit: number, currentUsage: number, pendingRequest?: { id: string, quotaId: string, requestedFactor: number, reason: string, status: string, requestedByDisplay: string, decidedByDisplay?: string | null, decidedAt?: string | null, decisionNote: string, createdAt: string } | null }> };

export type RequestQuotaIncreaseMutationVariables = Exact<{
  input: RequestQuotaIncreaseInput;
}>;


export type RequestQuotaIncreaseMutation = { requestQuotaIncrease: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, quotaId: string, requestedFactor: number, reason: string, status: string, requestedByDisplay: string, decidedByDisplay?: string | null, decidedAt?: string | null, decisionNote: string, createdAt: string } | null } };

export type ListBudgetsQueryVariables = Exact<{ [key: string]: never; }>;


export type ListBudgetsQuery = { astroliftBudgets: Array<{ id: string, scopeKind: string, scopeId: string, amountCents: number, currency: string, period: string, currentSpendCents: number, alertsAtPct: Array<number> }> };

export type ListCostSnapshotsQueryVariables = Exact<{
  days?: InputMaybe<Scalars['Int']['input']>;
  window?: InputMaybe<CostWindow>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListCostSnapshotsQuery = { astroliftCostSnapshots: Array<{ id: string, projectId?: string | null, registeredAppId?: string | null, managedServiceBindingId?: string | null, takenAt: any, by: string, amountCents: number, currency: string, source: string }> };

export type GetCostTrendQueryVariables = Exact<{
  window?: InputMaybe<CostWindow>;
  days?: InputMaybe<Scalars['Int']['input']>;
}>;


export type GetCostTrendQuery = { astroliftCostTrend: Array<{ date: any, amountCents: number, currency: string, isAnomaly: boolean }> };

export type GetCostForecastQueryVariables = Exact<{ [key: string]: never; }>;


export type GetCostForecastQuery = { astroliftCostForecast: { mtdCents: number, previousMonthCents: number, deltaPct: number, projectedMonthlyCents: number, confidence: ForecastConfidence, currency: string } };

export type GetCostByBindingQueryVariables = Exact<{
  window?: InputMaybe<CostWindow>;
  days?: InputMaybe<Scalars['Int']['input']>;
  registeredAppSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type GetCostByBindingQuery = { astroliftCostByBinding: { unattributedCents: number, totalCents: number, currency: string, attributedRows: Array<{ managedServiceBindingId?: string | null, managedServiceId?: string | null, managedServiceName?: string | null, managedServiceKind?: string | null, registeredAppSlug?: string | null, by: string, amountCents: number, currency: string }> } };

export type RegisterTenantClusterMutationVariables = Exact<{
  input: RegisterTenantClusterInput;
}>;


export type RegisterTenantClusterMutation = { registerTenantCluster: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, name: string, providerPluginSlug: string, region: string, authMethod: string, ingressClass: string, isActive: boolean } | null } };

export type UnregisterTenantClusterMutationVariables = Exact<{
  input: UnregisterTenantClusterInput;
}>;


export type UnregisterTenantClusterMutation = { unregisterTenantCluster: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { id: string, deleted: boolean } | null } };

export type CreateManagedDomainMutationVariables = Exact<{
  input: CreateManagedDomainInput;
}>;


export type CreateManagedDomainMutation = { createManagedDomain: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, zone: string, dnsDriver: string, defaultFor: string, isWildcardManaged: boolean } | null } };

export type SoftDeleteManagedDomainMutationVariables = Exact<{
  input: SoftDeleteManagedDomainInput;
}>;


export type SoftDeleteManagedDomainMutation = { softDeleteManagedDomain: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { id: string, deleted: boolean } | null } };

export type ClusterCountQueryVariables = Exact<{ [key: string]: never; }>;


export type ClusterCountQuery = { astroliftClusterCount: number };

export type ListClustersQueryVariables = Exact<{ [key: string]: never; }>;


export type ListClustersQuery = { astroliftClusters: Array<{ id: string, slug: string, name: string, organizationSlug?: string | null, providerPluginSlug: string, region: string, endpoint: string, authMethod: string, ingressClass: string, isActive: boolean, capabilities: Record<string, unknown>, capabilitiesProbedAt?: string | null, createdAt: string, lifecycle: string, lastManagementError: string, managedAt?: string | null, lastBootstrapRun?: { id: string, status: string, chartVersion: string, installedReleases: Record<string, unknown>, cliVersion: string, errorMessage: string, startedAt: string, endedAt: string, triggeredByUsername?: string | null } | null }> };

export type ClusterBootstrapRunsQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ClusterBootstrapRunsQuery = { astroliftClusters: Array<{ id: string, slug: string, bootstrapRuns: Array<{ id: string, status: string, chartVersion: string, installedReleases: Record<string, unknown>, cliVersion: string, errorMessage: string, startedAt: string, endedAt: string, triggeredByUsername?: string | null }> }> };

export type RecordClusterBootstrapRunMutationVariables = Exact<{
  input: RecordClusterBootstrapRunInput;
}>;


export type RecordClusterBootstrapRunMutation = { recordClusterBootstrapRun: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string } | null } };

export type DecommissionClusterMutationVariables = Exact<{
  input: DecommissionClusterInputType;
}>;


export type DecommissionClusterMutation = { decommissionCluster: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, lifecycle: string, lastManagementError: string } | null } };

export type BringClusterIntoManagementMutationVariables = Exact<{
  input: BringClusterIntoManagementInputType;
}>;


export type BringClusterIntoManagementMutation = { bringClusterIntoManagement: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, lifecycle: string, lastManagementError: string, managedAt?: string | null } | null } };

export type RefreshClusterManagementMutationVariables = Exact<{
  input: RefreshClusterManagementInputType;
}>;


export type RefreshClusterManagementMutation = { refreshClusterManagement: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, lifecycle: string, lastManagementError: string, managedAt?: string | null } | null } };

export type ListManagedDomainsQueryVariables = Exact<{ [key: string]: never; }>;


export type ListManagedDomainsQuery = { astroliftManagedDomains: Array<{ id: string, zone: string, organizationSlug?: string | null, dnsDriver: string, defaultFor: string, isWildcardManaged: boolean, createdAt: string }> };

export type ClusterHealthQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
  eventLimit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ClusterHealthQuery = { astroliftClusterHealth?: { clusterId: string, pods: Array<{ namespace: string, phase: string, count: number }>, events: Array<{ namespace: string, name: string, reason: string, message: string, type: string, count: number, firstSeen: string, lastSeen: string, involvedObject: string }> } | null };

export type RecentClusterWorkflowsQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type RecentClusterWorkflowsQuery = { astroliftRecentClusterWorkflows: Array<{ workflowId: string, workflowType: string, status: string, startedAt: string, closedAt: string, runId: string }> };

export type ClusterLifecycleAuditQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ClusterLifecycleAuditQuery = { astroliftClusterLifecycleAudit: Array<{ operation: string, variables: Record<string, unknown>, success: boolean, errors: Array<string>, timestamp: string, actor?: string | null }> };

export type ClusterAppCountQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterAppCountQuery = { astroliftAppCountForCluster: number };

export type ClusterBootstrapPlanQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterBootstrapPlanQuery = { astroliftClusterBootstrapPlan?: { clusterId: string, providerPluginSlug: string, components: Array<{ key: string, title: string, defaultEnabled: boolean, rationale: string, helmValues: Record<string, unknown>, requires: Array<string>, options: Array<{ key: string, label: string, default: string, choices: Array<{ value: string, label: string }> }> }> } | null };

export type InstallClusterPrereqsMutationVariables = Exact<{
  input: InstallClusterPrereqsInputType;
}>;


export type InstallClusterPrereqsMutation = { installClusterPrereqs: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, lifecycle: string, lastManagementError: string } | null } };

export type ListProviderPluginsQueryVariables = Exact<{ [key: string]: never; }>;


export type ListProviderPluginsQuery = { astroliftProviderPlugins: Array<{ id: string, slug: string, name: string, version: string, capabilitiesManifest: Record<string, unknown>, isEnabled: boolean }> };

export type ClusterWorkloadHealthQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterWorkloadHealthQuery = { astroliftClusterWorkloadHealth: Array<{ namespace: string, workloadName: string, desiredReplicas: number, readyReplicas: number, restartCount24h: number, lastImageDeployedAt: string }> };

export type ListEnvironmentsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListEnvironmentsQuery = { astroliftEnvironments: Array<{ id: string, name: string, url: string, deploysPaused: boolean, ingressPaused: boolean, requiredApprovals: number, registeredAppSlug: string, clusterSlug?: string | null, domainZone?: string | null, createdAt: string, settings: Array<{ id: string, key: string, value: string }> }> };

export type ListDeploymentsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListDeploymentsQuery = { astroliftDeployments: Array<{ id: string, registeredAppSlug: string, environmentName: string, workloadSlug?: string | null, triggerKind: string, strategy: string, status: string, imageTag: string, imageDigest: string, clusterRevision: string, approvalsRequired: number, approvalsReceived: number, requiredApproverCount: number, startedAt?: string | null, succeededAt?: string | null, failedAt?: string | null, endedAt?: string | null, durationSeconds?: number | null, createdAt: string, commitSha: string, commitMessage: string, commitAuthor: string, branch: string, ciActorKind: string, ciProvider: string, ciRunUrl: string, repoUrl: string, abortedReason: string, triggeredByUserId?: string | null, triggeredByMe: boolean, approvedBy: Array<{ userId: string, displayName: string, email: string, approvedAt?: string | null, mailtoUrl: string }>, awaitingApprovers: Array<{ userId: string, displayName: string, email: string, approvedAt?: string | null, mailtoUrl: string }> }> };

export type GetDeploymentQueryVariables = Exact<{
  id: Scalars['String']['input'];
}>;


export type GetDeploymentQuery = { astroliftDeployment?: { id: string, registeredAppSlug: string, environmentName: string, workloadSlug?: string | null, triggerKind: string, strategy: string, status: string, imageTag: string, imageDigest: string, clusterRevision: string, approvalsRequired: number, approvalsReceived: number, requiredApproverCount: number, startedAt?: string | null, succeededAt?: string | null, failedAt?: string | null, endedAt?: string | null, durationSeconds?: number | null, createdAt: string, ciActorKind: string, commitSha: string, commitMessage: string, commitAuthor: string, branch: string, ciRunUrl: string, ciProvider: string, repoUrl: string, abortedReason: string, triggeredByUserId?: string | null, triggeredByMe: boolean, approvedBy: Array<{ userId: string, displayName: string, email: string, approvedAt?: string | null, mailtoUrl: string }>, awaitingApprovers: Array<{ userId: string, displayName: string, email: string, approvedAt?: string | null, mailtoUrl: string }> } | null };

export type GetDeploymentReleaseNotesQueryVariables = Exact<{
  deploymentId: Scalars['String']['input'];
}>;


export type GetDeploymentReleaseNotesQuery = { astroliftDeploymentReleaseNotes?: { baseSha: string, headSha: string, compareUrl: string, commits: Array<{ sha: string, subject: string, author: string, isMerge: boolean }>, pullRequests: Array<{ number: number, title: string, body: string, author: string, mergedAt?: string | null, prUrl: string }> } | null };

export type GetDeploymentApprovalHistoryQueryVariables = Exact<{
  deploymentId: Scalars['String']['input'];
}>;


export type GetDeploymentApprovalHistoryQuery = { astroliftDeploymentApprovalHistory: Array<{ id: string, action: string, decision: string, actorKind: string, actorId: string, actorDisplay: string, occurredAt: string, reason: string }> };

export type GetDeploymentLogQueryVariables = Exact<{
  deploymentId: Scalars['String']['input'];
}>;


export type GetDeploymentLogQuery = { astroliftDeploymentLog: Array<{ id: string, deploymentId: string, status: string, message: string, detail: Record<string, unknown>, occurredAt: string }> };

export type GetDeploymentMetricsQueryVariables = Exact<{
  windowDays?: InputMaybe<Scalars['Int']['input']>;
}>;


export type GetDeploymentMetricsQuery = { astroliftDeploymentMetrics: { windowDays: number, total: number, succeeded: number, failed: number, rolledBack: number, inFlight: number, successRate: number, meanDurationSeconds?: number | null, p95DurationSeconds?: number | null } };

export type ListScheduledJobRunsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListScheduledJobRunsQuery = { astroliftScheduledJobRuns: Array<{ id: string, registeredAppSlug: string, environmentName: string, workloadSlug: string, k8sJobName: string, status: string, startedAt?: string | null, endedAt?: string | null, durationSeconds?: number | null, exitCode?: number | null, logExcerpt: string, output: string, createdAt: string }> };

export type ListCommandRunsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListCommandRunsQuery = { astroliftCommandRuns: Array<{ id: string, registeredAppSlug: string, workloadSlug?: string | null, invokedByUsername?: string | null, command: Record<string, unknown>, startedAt?: string | null, endedAt?: string | null, exitCode?: number | null, logExcerpt: string, output: string, createdAt: string }> };

export type ListAppHealthSummaryQueryVariables = Exact<{ [key: string]: never; }>;


export type ListAppHealthSummaryQuery = { astroliftAppHealthSummary: Array<{ appSlug: string, appName: string, environmentCount: number, latestDeploymentStatus?: string | null, latestImageTag: string, lastDeployedAt?: string | null, hasRecentFailure: boolean }> };

export type ListAppDomainsQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
}>;


export type ListAppDomainsQuery = { astroliftAppDomains: Array<{ id: string, hostname: string, certState: string, validationMethod: string, validationToken: string, lastCheckedAt?: string | null, isActive: boolean, registeredAppSlug: string, createdAt: string, txtChallengeToken: string, expectedCnameTarget: string, isPlatformManagedZone: boolean, lastValidationError: string, certificateState: string, lastCertificateError: string, byoCertificateUploadedAt?: string | null, certExpiresAt?: string | null, certIssuerSerial: string, certObservabilityStatus: string, requiredDnsRecords: Array<{ kind: string, name: string, value: string, ttl: number, propagated: boolean, lastCheckedAt?: string | null, message: string }> }> };

export type ListAppDeployTokensQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
}>;


export type ListAppDeployTokensQuery = { astroliftAppDeployTokens: Array<{ id: string, name: string, last4: string, scopes: Array<string>, expiresAt?: string | null, lastUsedAt?: string | null, lastUsedIp: string, lastUsedAgent: string, isRevoked: boolean, lastRotatedAt?: string | null, createdAt: string }> };

export type ListPreviewEnvironmentsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListPreviewEnvironmentsQuery = { astroliftPreviewEnvironments: Array<{ id: string, registeredAppSlug: string, prNumber: number, branch: string, commitSha: string, status: string, hostname: string, namespace: string, lastDeployedAt?: string | null, tornDownAt?: string | null, ttlUntil: string, sourceUrl: string, prUrl: string, estimatedDailyCostUsd?: number | null, aggregateResources: { cpuCores: number, memoryBytes: number, podCount: number } }> };

export type ListAppPodsQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListAppPodsQuery = { astroliftAppPods: Array<{ name: string, workload: string, status: string, phase: string, ready: boolean, restarts: number, age?: string | null, node: string, containerStatuses: Array<{ name: string, ready: boolean, restarts: number, image: string, state: string, waitingReason: string, terminatedReason: string, kind: string, lastRestartReasons: Array<string>, lastRestartAt?: string | null, resources: { cpuRequest: string, cpuLimit: string, memoryRequest: string, memoryLimit: string } }>, recentErrorEvent?: { reason: string, message: string, type: string, count: number, lastSeen: string } | null }> };

export type GetWorkloadPodStatusBreakdownQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  workloadSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type GetWorkloadPodStatusBreakdownQuery = { astroliftWorkloadPodStatusBreakdown: Array<{ status: string, count: number, percent: number, pods: Array<{ name: string, age?: string | null, ready: boolean }> }> };

export type ListAppDnsRecordsQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListAppDnsRecordsQuery = { astroliftAppDnsRecords: Array<{ name: string, type: string, value: string, ttl: number, propagationStatus: string }> };

export type ListAppCertificatesQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListAppCertificatesQuery = { astroliftAppCertificates: Array<{ id: string, hostname: string, issuer: string, notAfter: string, daysUntilExpiry: number, renewalStatus: string }> };

export type GetAppIdentityBindingQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type GetAppIdentityBindingQuery = { astroliftAppIdentityBinding?: { kind: string, roleArnOrPrincipal: string, trustPolicySummary: string, lastUsedAt?: string | null } | null };

export type PreviewDeregisterAppQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
}>;


export type PreviewDeregisterAppQuery = { previewAstroliftDeregister?: { appSlug: string, appName: string, totalResourceCount: number, registryRepoUri: string, k8sObjects: Array<{ clusterSlug: string, namespace: string, apiVersion: string, kind: string, name: string }>, managedServices: Array<{ id: string, name: string, kind: string, variant: string, environmentName: string, status: string }>, secretRefs: Array<{ id: string, bundleSlug: string, environmentName: string, clusterSlug?: string | null, prefix: string }>, deployTokens: Array<{ id: string, name: string, last4: string, environmentName?: string | null }>, sourceWebhook?: { installed: boolean, repo: string, hookId: string } | null, identityRoles: Array<{ clusterSlug: string, kind: string, roleArnOrPrincipal: string }> } | null };

export type PreviewForceRedeployQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type PreviewForceRedeployQuery = { previewAstroliftForceRedeploy?: { appSlug: string, environmentName?: string | null, inFlightDeployments: Array<{ id: string, environmentName: string, workloadSlug?: string | null, status: string, imageTag: string, startedAt?: string | null, createdAt: string, triggerKind: string, triggeredByDisplay: string, ciActorKind: string, ciRunUrl: string }> } | null };

export type DeploymentLifecycleStreamSubscriptionVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type DeploymentLifecycleStreamSubscription = { astroliftDeploymentLifecycleStream: { deploymentId: string, registeredAppSlug: string, environmentName: string, status: string, occurredAt: string } };

export type OnAppLogSubscriptionVariables = Exact<{
  appSlug: Scalars['String']['input'];
  podName: Scalars['String']['input'];
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
  container?: InputMaybe<Scalars['String']['input']>;
  follow?: InputMaybe<Scalars['Boolean']['input']>;
  tailLines?: InputMaybe<Scalars['Int']['input']>;
}>;


export type OnAppLogSubscription = { astroliftOnAppLog: { podName: string, container: string, timestamp: string, message: string, stream: string } };

export type OnAppLogsSubscriptionVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
  container?: InputMaybe<Scalars['String']['input']>;
  follow?: InputMaybe<Scalars['Boolean']['input']>;
  tailLines?: InputMaybe<Scalars['Int']['input']>;
}>;


export type OnAppLogsSubscription = { astroliftOnAppLogs: { podName: string, container: string, timestamp: string, message: string, stream: string } };

export type CreateWebhookMutationVariables = Exact<{
  input: CreateWebhookSubscriptionInput;
}>;


export type CreateWebhookMutation = { createWebhookSubscription: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { plaintextSecret: string, subscription: { id: string, url: string, events: Array<string>, isActive: boolean, format: string, createdAt: string, failureCount: number, secretRotatedAt?: string | null } } | null } };

export type UpdateWebhookMutationVariables = Exact<{
  input: UpdateWebhookSubscriptionInput;
}>;


export type UpdateWebhookMutation = { updateWebhookSubscription: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null, currentVersion?: number | null, requestedVersion?: number | null }>, data?: { id: string, url: string, events: Array<string>, isActive: boolean, format: string, secretRotatedAt?: string | null, version: number } | null } };

export type DeleteWebhookMutationVariables = Exact<{
  input: DeleteWebhookSubscriptionInput;
}>;


export type DeleteWebhookMutation = { deleteWebhookSubscription: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { id: string, deleted: boolean } | null } };

export type TestFireWebhookMutationVariables = Exact<{
  input: TestWebhookInput;
}>;


export type TestFireWebhookMutation = { testWebhookSubscription: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { subscriptionId: string, url: string, delivered: boolean, statusCode?: number | null, durationMs: number, responseBodyExcerpt: string, error: string, deliveryId: string, timestamp: string } | null } };

export type RotateOutboundWebhookSecretMutationVariables = Exact<{
  input: RotateOutboundWebhookSecretInput;
}>;


export type RotateOutboundWebhookSecretMutation = { rotateOutboundWebhookSecret: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { plaintextSecret: string, subscription: { id: string, url: string, events: Array<string>, isActive: boolean, format: string, createdAt: string, failureCount: number, secretRotatedAt?: string | null } } | null } };

export type MarkAllNotificationsReadMutationVariables = Exact<{ [key: string]: never; }>;


export type MarkAllNotificationsReadMutation = { markAllNotificationsRead: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { marked: number } | null } };

export type MarkNotificationReadMutationVariables = Exact<{
  input: MarkNotificationReadInput;
}>;


export type MarkNotificationReadMutation = { markNotificationRead: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { id: string, readAt?: string | null } | null } };

export type ExportAuditEventsMutationVariables = Exact<{
  input: ExportAuditEventsInput;
}>;


export type ExportAuditEventsMutation = { exportAuditEvents: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, format: string, rowCount: number, byteCount: number, sha256: string, downloadUrl: string, expiresAt: string, createdAt: string } | null } };

export type ExportAstroliftAppLogsMutationVariables = Exact<{
  input: ExportAppLogsInput;
}>;


export type ExportAstroliftAppLogsMutation = { exportAstroliftAppLogs: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, format: string, status: string, rowCount: number, byteCount: number, sha256: string, truncated: boolean, downloadUrl: string, expiresAt: string, createdAt: string, errorMessage: string } | null } };

export type GetRecentActivityQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
  cursor?: InputMaybe<Scalars['String']['input']>;
}>;


export type GetRecentActivityQuery = { astroliftRecentActivity: { nextCursor?: string | null, items: Array<{ id: string, eventType: string, action: string, actorDisplay: string, targetKind: string, targetLabel: string, targetHref?: string | null, occurredAt: string, payload: Record<string, unknown> }> } };

export type ListEventsQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
  eventType?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListEventsQuery = { astroliftEvents: Array<{ id: string, eventType: string, payload: Record<string, unknown>, organizationId?: string | null, teamId?: string | null, projectId?: string | null, registeredAppId?: string | null, occurredAt: string, resourceKind: string, resourceId: string }> };

export type ListEventsAggregatedQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
  eventType?: InputMaybe<Scalars['String']['input']>;
  aggregateWindowSeconds?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListEventsAggregatedQuery = { astroliftEventsAggregated: Array<{ count: number, firstAt: string, lastAt: string, eventType: string, resourceKind: string, resourceId: string, representative: { id: string, eventType: string, payload: Record<string, unknown>, organizationId?: string | null, teamId?: string | null, projectId?: string | null, registeredAppId?: string | null, occurredAt: string, resourceKind: string, resourceId: string } }> };

export type ListAuditEventsQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
  action?: InputMaybe<Scalars['String']['input']>;
  decision?: InputMaybe<Scalars['String']['input']>;
  actorId?: InputMaybe<Scalars['String']['input']>;
  createdAtGte?: InputMaybe<Scalars['DateTime']['input']>;
  createdAtLte?: InputMaybe<Scalars['DateTime']['input']>;
}>;


export type ListAuditEventsQuery = { astroliftAuditEvents: Array<{ id: string, organizationId?: string | null, occurredAt: string, actorKind: string, actorId: string, actorDisplay: string, action: string, decision: string, targetKind: string, targetId: string, targetSlug: string, requestId: string, data: Record<string, unknown>, before?: Record<string, unknown> | null, after?: Record<string, unknown> | null }> };

export type ListAuditEventsPageQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
  after?: InputMaybe<Scalars['String']['input']>;
  action?: InputMaybe<Scalars['String']['input']>;
  decision?: InputMaybe<Scalars['String']['input']>;
  actorId?: InputMaybe<Scalars['String']['input']>;
  createdAtGte?: InputMaybe<Scalars['DateTime']['input']>;
  createdAtLte?: InputMaybe<Scalars['DateTime']['input']>;
  includeTotal?: InputMaybe<Scalars['Boolean']['input']>;
}>;


export type ListAuditEventsPageQuery = { astroliftAuditEventsPage: { nextCursor?: string | null, totalCount?: number | null, items: Array<{ id: string, organizationId?: string | null, occurredAt: string, actorKind: string, actorId: string, actorDisplay: string, action: string, decision: string, targetKind: string, targetId: string, targetSlug: string, requestId: string, data: Record<string, unknown>, before?: Record<string, unknown> | null, after?: Record<string, unknown> | null }> } };

export type GetAuditRetentionQueryVariables = Exact<{ [key: string]: never; }>;


export type GetAuditRetentionQuery = { astroliftAuditRetention: { days: number } };

export type ListWebhooksQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListWebhooksQuery = { astroliftWebhookSubscriptions: Array<{ id: string, url: string, events: Array<string>, isActive: boolean, format: string, lastDeliveryAt?: string | null, lastResponseStatus?: number | null, failureCount: number, secretRotatedAt?: string | null, createdAt: string, version: number }> };

export type ListWebhookDeliveriesQueryVariables = Exact<{
  subscriptionId: Scalars['GUID']['input'];
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListWebhookDeliveriesQuery = { astroliftWebhookDeliveries: Array<{ id: string, subscriptionId: string, eventType: string, retryAttempt: number, statusCode?: number | null, latencyMs: number, success: boolean, isTest: boolean, requestPayloadExcerpt: string, responseBodyExcerpt: string, error: string, deliveryId: string, deliveredAt: string }> };

export type ListMyNotificationsQueryVariables = Exact<{
  unreadOnly?: InputMaybe<Scalars['Boolean']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListMyNotificationsQuery = { astroliftMyNotifications: Array<{ id: string, userId: string, kind: string, title: string, body: string, link: string, readAt?: string | null, createdAt: string }> };

export type ListWorkflowRunsQueryVariables = Exact<{
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListWorkflowRunsQuery = { astroliftWorkflowRuns: Array<{ id: string, workflowKind: string, workflowId: string, runId: string, status: string, startedAt?: string | null, endedAt?: string | null, organizationId?: string | null, registeredAppId?: string | null, failure: Record<string, unknown> }> };

export type GetMyPermissionsQueryVariables = Exact<{ [key: string]: never; }>;


export type GetMyPermissionsQuery = { astroliftMyPermissions: Array<string> };

export type ConnectSourceMutationVariables = Exact<{
  input: ConnectSourceInput;
}>;


export type ConnectSourceMutation = { connectSource: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, kind: string, name: string, displayName: string, accountLogin: string, repoVisibilityScopes: Array<string>, isOauthAppConfig: boolean, isActive: boolean, createdAt: string } | null } };

export type UpdateSourceConnectionMutationVariables = Exact<{
  input: UpdateSourceConnectionInput;
}>;


export type UpdateSourceConnectionMutation = { updateSourceConnection: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, displayName: string, repoVisibilityScopes: Array<string>, isActive: boolean, appClientId: string, needsClientId: boolean } | null } };

export type DisconnectSourceMutationVariables = Exact<{
  input: DisconnectSourceInput;
}>;


export type DisconnectSourceMutation = { disconnectSource: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { id: string, isActive: boolean } | null } };

export type GenerateSshDeployKeyMutationVariables = Exact<{
  input: GenerateSshDeployKeyInput;
}>;


export type GenerateSshDeployKeyMutation = { generateSshDeployKey: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { key: { id: string, name: string, publicKey: string, fingerprintSha256: string, registeredAppSlug?: string | null, createdAt: string } } | null } };

export type RotateWebhookSecretMutationVariables = Exact<{
  input: RotateWebhookSecretInput;
}>;


export type RotateWebhookSecretMutation = { rotateWebhookSecret: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { connectionId: string, plaintextSecret: string, webhookUrlPath: string } | null } };

export type DeleteSshDeployKeyMutationVariables = Exact<{
  input: DeleteSshDeployKeyInput;
}>;


export type DeleteSshDeployKeyMutation = { deleteSshDeployKey: { ok: boolean, errors: Array<{ code: string, message: string }>, data?: { id: string } | null } };

export type PushCiWorkflowMutationVariables = Exact<{
  input: PushCiWorkflowInput;
}>;


export type PushCiWorkflowMutation = { pushCiWorkflow: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { commitSha: string, filePath: string, repoUrl: string } | null } };

export type ListAppSecretsQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListAppSecretsQuery = { astroliftAppSecrets: Array<{ id: string, key: string, environmentName: string, source: string, bundleSlug: string, managedServiceKind: string, isMasked: boolean, lastEditedAt?: string | null, expiresAt?: string | null, setVia: string, lastEditedBy?: { id: string, username: string, displayName: string } | null }> };

export type GetAppVersionQueryVariables = Exact<{
  slug: Scalars['String']['input'];
}>;


export type GetAppVersionQuery = { astroliftApp?: { id: string, version: number } | null };

export type ListAppSecretBundleAttachmentsQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListAppSecretBundleAttachmentsQuery = { astroliftAppSecretBundleAttachments: Array<{ id: string, registeredAppSlug: string, environmentName: string, bundleSlug: string, bundleName: string, prefix: string, teamSlug?: string | null, keyCount: number, mergeOrder: number, attachedAt?: string | null }> };

export type ListSecretBundlesQueryVariables = Exact<{ [key: string]: never; }>;


export type ListSecretBundlesQuery = { astroliftSecretBundles: Array<{ id: string, slug: string, name: string, backendRef: string, createdAt: string, keyCount: number, lastKnownKeysAt?: string | null }> };

export type ListManagedServicesQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListManagedServicesQuery = { astroliftManagedServices: Array<{ id: string, kind: string, name: string, variant: string, environmentName: string, registeredAppSlug: string, status: string, statusError: string, config: Record<string, unknown>, createdAt: string, updatedAt: string, lastActionAt?: string | null, lastActionKind: string, editableFields: Array<string> }> };

export type ListManagedServiceObjectsQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListManagedServiceObjectsQuery = { astroliftManagedServiceObjects?: { managedServiceId: string, kind: string, name: string, truncated: boolean, cacheAgeSeconds?: number | null, objects: Array<{ key: string, sizeBytes: number, lastModified?: string | null }> } | null };

export type GetManagedServiceQueueDepthQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
}>;


export type GetManagedServiceQueueDepthQuery = { astroliftManagedServiceQueueDepth?: { managedServiceId: string, kind: string, name: string, depth: number, inFlight: number, sampledAt?: string | null } | null };

export type GetEmailServiceDetailQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
}>;


export type GetEmailServiceDetailQuery = { astroliftEmailServiceDetail?: { managedServiceId: string, pluginSlug: string, region: string, identity: string, unsupportedNotes: Array<string>, quota?: { maxSendRate: number, max24HourSend: number, sentLast24h: number } | null, accountStatus?: { sendingEnabled: boolean, productionAccess: boolean, reputationScore?: number | null, bounceRatePct?: number | null, complaintRatePct?: number | null } | null, identityVerification?: { identity: string, isDomain: boolean, status: string, verificationToken: string, dkimTokens: Array<{ token: string, cnameHost: string, cnameTarget: string }> } | null, dnsAuthStatus?: { identity: string, checkedAt: string, overall: string, dkim: { protocol: string, outcome: string, records: Array<string>, message: string }, spf: { protocol: string, outcome: string, records: Array<string>, message: string }, dmarc: { protocol: string, outcome: string, records: Array<string>, message: string } } | null, suppressionEntries: Array<{ address: string, reason: string, suppressedAt: string, detail: string }> } | null };

export type SecretChangeProposalFieldsFragment = { id: string, registeredAppSlug: string, environmentName: string, op: string, status: string, proposerUserId: string, proposerDisplayName: string, payload: Record<string, unknown>, payloadDiff: Record<string, unknown>, requiredApproverCount: number, approvalsCount: number, expiresAt: string, decidedAt?: string | null, appliedAt?: string | null, applyError: string, createdAt: string, approvals: Array<{ id: string, approverUserId: string, approverDisplayName: string, decision: string, decidedAt: string, reason: string }> };

export type ListSecretChangeProposalsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
  status?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListSecretChangeProposalsQuery = { astroliftSecretChangeProposals: Array<{ id: string, registeredAppSlug: string, environmentName: string, op: string, status: string, proposerUserId: string, proposerDisplayName: string, payload: Record<string, unknown>, payloadDiff: Record<string, unknown>, requiredApproverCount: number, approvalsCount: number, expiresAt: string, decidedAt?: string | null, appliedAt?: string | null, applyError: string, createdAt: string, approvals: Array<{ id: string, approverUserId: string, approverDisplayName: string, decision: string, decidedAt: string, reason: string }> }> };

export type GetSecretChangeProposalQueryVariables = Exact<{
  id: Scalars['GUID']['input'];
}>;


export type GetSecretChangeProposalQuery = { astroliftSecretChangeProposal?: { id: string, registeredAppSlug: string, environmentName: string, op: string, status: string, proposerUserId: string, proposerDisplayName: string, payload: Record<string, unknown>, payloadDiff: Record<string, unknown>, requiredApproverCount: number, approvalsCount: number, expiresAt: string, decidedAt?: string | null, appliedAt?: string | null, applyError: string, createdAt: string, approvals: Array<{ id: string, approverUserId: string, approverDisplayName: string, decision: string, decidedAt: string, reason: string }> } | null };

export type GetAppSecretHistoryQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
  key: Scalars['String']['input'];
}>;


export type GetAppSecretHistoryQuery = { astroliftAppSecretHistory: Array<{ timestamp: string, action: string, success: boolean, errorCode: string, sourceIp: string, actor: { id: string, username: string } }> };

export type CreateWorkflowDefinitionMutationVariables = Exact<{
  name: Scalars['String']['input'];
  slug: Scalars['String']['input'];
  modelLabel: Scalars['String']['input'];
  states: Scalars['JSON']['input'];
  transitions: Scalars['JSON']['input'];
  description?: InputMaybe<Scalars['String']['input']>;
  isEnabled?: InputMaybe<Scalars['Boolean']['input']>;
}>;


export type CreateWorkflowDefinitionMutation = { createWorkflowDefinition: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type UpdateWorkflowDefinitionMutationVariables = Exact<{
  slug: Scalars['String']['input'];
  name?: InputMaybe<Scalars['String']['input']>;
  description?: InputMaybe<Scalars['String']['input']>;
  modelLabel?: InputMaybe<Scalars['String']['input']>;
  states?: InputMaybe<Scalars['JSON']['input']>;
  transitions?: InputMaybe<Scalars['JSON']['input']>;
  isEnabled?: InputMaybe<Scalars['Boolean']['input']>;
}>;


export type UpdateWorkflowDefinitionMutation = { updateWorkflowDefinition: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type DeleteWorkflowDefinitionMutationVariables = Exact<{
  slug: Scalars['String']['input'];
}>;


export type DeleteWorkflowDefinitionMutation = { deleteWorkflowDefinition: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type StartWorkflowMutationVariables = Exact<{
  workflowSlug: Scalars['String']['input'];
  modelLabel: Scalars['String']['input'];
  objectId: Scalars['Int']['input'];
}>;


export type StartWorkflowMutation = { startWorkflow: { ok: boolean, instanceId?: string | null, errors: Array<{ field: string, messages: Array<string> }> } };

export type TransitionWorkflowMutationVariables = Exact<{
  instanceId: Scalars['ID']['input'];
  toState: Scalars['String']['input'];
  note?: InputMaybe<Scalars['String']['input']>;
}>;


export type TransitionWorkflowMutation = { transitionWorkflow: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type CancelWorkflowInstanceMutationVariables = Exact<{
  workflowId: Scalars['String']['input'];
}>;


export type CancelWorkflowInstanceMutation = { cancelWorkflowInstance: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type TerminateWorkflowInstanceMutationVariables = Exact<{
  workflowId: Scalars['String']['input'];
  reason: Scalars['String']['input'];
}>;


export type TerminateWorkflowInstanceMutation = { terminateWorkflowInstance: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type SignalWorkflowInstanceMutationVariables = Exact<{
  workflowId: Scalars['String']['input'];
  signalName: Scalars['String']['input'];
  payload?: InputMaybe<Scalars['JSON']['input']>;
}>;


export type SignalWorkflowInstanceMutation = { signalWorkflowInstance: { ok: boolean, errors: Array<{ field: string, messages: Array<string> }> } };

export type GetWorkflowsQueryVariables = Exact<{
  modelLabel?: InputMaybe<Scalars['String']['input']>;
}>;


export type GetWorkflowsQuery = { workflowDefinitions: Array<{ name: string, slug: string, description?: string | null, modelLabel: string, isEnabled: boolean, createdAt: string, instanceCount: number, activeInstanceCount: number }> };

export type GetWorkflowQueryVariables = Exact<{
  slug: Scalars['String']['input'];
}>;


export type GetWorkflowQuery = { workflowDefinition?: { name: string, slug: string, description?: string | null, modelLabel: string, states: Record<string, unknown>, transitions: Record<string, unknown>, isEnabled: boolean, createdAt: string, instanceCount: number, activeInstanceCount: number } | null };

export type GetWorkflowInstancesQueryVariables = Exact<{
  workflowType?: InputMaybe<Scalars['String']['input']>;
  status?: InputMaybe<Scalars['String']['input']>;
  limit: Scalars['Int']['input'];
}>;


export type GetWorkflowInstancesQuery = { astroliftWorkflowInstances: { nextCursor?: string | null, items: Array<{ workflowId: string, workflowType: string, runId: string, status: string, startedAt: string, closedAt: string, durationSeconds?: number | null, taskQueue: string, triggeredBy: string }> } };

export type GetWorkflowInstanceDetailQueryVariables = Exact<{
  workflowId: Scalars['String']['input'];
}>;


export type GetWorkflowInstanceDetailQuery = { astroliftWorkflowInstanceDetail?: { instance: { workflowId: string, workflowType: string, runId: string, status: string, startedAt: string, closedAt: string, durationSeconds?: number | null, taskQueue: string, triggeredBy: string }, history: Array<{ eventType: string, timestamp: string, payload: Record<string, unknown>, retryCount: number, decision: string }> } | null };
