export type Maybe<T> = T | null;
export type InputMaybe<T> = Maybe<T>;
export type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
export type MakeOptional<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]?: Maybe<T[SubKey]> };
export type MakeMaybe<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]: Maybe<T[SubKey]> };
export type MakeEmpty<T extends { [key: string]: unknown }, K extends keyof T> = { [_ in K]?: never };
export type Incremental<T> = T | { [P in keyof T]?: P extends ' $fragmentName' | '__typename' ? T[P] : never };
/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string; }
  String: { input: string; output: string; }
  Boolean: { input: boolean; output: boolean; }
  Int: { input: number; output: number; }
  Float: { input: number; output: number; }
  /** Date (isoformat) */
  Date: { input: any; output: any; }
  /** Date with time (isoformat) */
  DateTime: { input: string; output: string; }
  /** UUID v7 — the platform's external identifier. */
  GUID: { input: string; output: string; }
  /** The `JSON` scalar type represents JSON values as specified by [ECMA-404](https://ecma-international.org/wp-content/uploads/ECMA-404_2nd_edition_december_2017.pdf). */
  JSON: { input: Record<string, unknown>; output: Record<string, unknown>; }
  UUID: { input: any; output: any; }
};

export type AbortDeploymentInput = {
  id: Scalars['GUID']['input'];
  reason: Scalars['String']['input'];
};

export type AcceptInvitationInput = {
  token: Scalars['String']['input'];
};

export type AcknowledgeAlertEventInput = {
  id: Scalars['GUID']['input'];
};

export type AddAppDomainInput = {
  appSlug: Scalars['String']['input'];
  hostname: Scalars['String']['input'];
  validationMethod: InputMaybe<Scalars['String']['input']>;
};

export type AddOrganizationAllowlistDomainInput = {
  defaultRoleSlug: InputMaybe<Scalars['String']['input']>;
  domain: Scalars['String']['input'];
  requiresReview: Scalars['Boolean']['input'];
};

export type Alertruledeletedpayload = {
  deleted: Scalars['Boolean']['output'];
  id: Scalars['GUID']['output'];
};

export type AlertruledeletedpayloadMutationResult = {
  data?: Maybe<Alertruledeletedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type Appdomainremovedpayload = {
  deleted: Scalars['Boolean']['output'];
  id: Scalars['GUID']['output'];
};

export type AppdomainremovedpayloadMutationResult = {
  data?: Maybe<Appdomainremovedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type ApproveByTokenInput = {
  token: Scalars['String']['input'];
};

export type Appsecretwritepayload = {
  appSlug: Scalars['String']['output'];
  key: Scalars['String']['output'];
  rawManifestStaged: Scalars['String']['output'];
};

export type AppsecretwritepayloadMutationResult = {
  data?: Maybe<Appsecretwritepayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type ArchiveAppRegistryRepoInput = {
  appId: Scalars['GUID']['input'];
  archive: Scalars['Boolean']['input'];
};

export type AssignAppToProjectInput = {
  appSlug: Scalars['String']['input'];
  projectGuid: InputMaybe<Scalars['GUID']['input']>;
};

export type AstroliftActiveSession = {
  createdAt?: Maybe<Scalars['DateTime']['output']>;
  expiresAt: Scalars['DateTime']['output'];
  id: Scalars['String']['output'];
  ipAddress?: Maybe<Scalars['String']['output']>;
  isCurrent: Scalars['Boolean']['output'];
  lastSeenAt?: Maybe<Scalars['DateTime']['output']>;
  userAgent?: Maybe<Scalars['String']['output']>;
};

export type AstroliftActivityItem = {
  action: Scalars['String']['output'];
  actorDisplay: Scalars['String']['output'];
  eventType: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  occurredAt: Scalars['DateTime']['output'];
  payload: Scalars['JSON']['output'];
  targetHref?: Maybe<Scalars['String']['output']>;
  targetKind: Scalars['String']['output'];
  targetLabel: Scalars['String']['output'];
};

export type AstroliftActivityPage = {
  items: Array<AstroliftActivityItem>;
  nextCursor?: Maybe<Scalars['String']['output']>;
};

export type AstroliftAlertEvent = {
  acknowledgedAt?: Maybe<Scalars['DateTime']['output']>;
  detail: Scalars['JSON']['output'];
  firedAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  resolvedAt?: Maybe<Scalars['DateTime']['output']>;
  ruleId: Scalars['GUID']['output'];
  severity: Scalars['String']['output'];
  summary: Scalars['String']['output'];
};

export type AstroliftAlertEventMutationResult = {
  data?: Maybe<AstroliftAlertEvent>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAlertRule = {
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  notifyChannels: Scalars['JSON']['output'];
  organizationSlug: Scalars['String']['output'];
  predicate: Scalars['JSON']['output'];
  severity: Scalars['String']['output'];
  target: Scalars['String']['output'];
  targetId: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftAlertRuleMutationResult = {
  data?: Maybe<AstroliftAlertRule>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAnonymizeUserInput = {
  userGid: Scalars['GUID']['input'];
};

export type AstroliftAnonymizeUserPayload = {
  anonymizedAt: Scalars['DateTime']['output'];
  anonymizedUserId: Scalars['GUID']['output'];
  lifecycle: Scalars['String']['output'];
  requiresLogout: Scalars['Boolean']['output'];
  wasSelf: Scalars['Boolean']['output'];
};

export type AstroliftAnonymizeUserPayloadMutationResult = {
  data?: Maybe<AstroliftAnonymizeUserPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftApiToken = {
  createdAt: Scalars['DateTime']['output'];
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  isRevoked: Scalars['Boolean']['output'];
  lastUsedAt?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  scopes: Array<Scalars['String']['output']>;
  teamSlug?: Maybe<Scalars['String']['output']>;
  tokenLast4: Scalars['String']['output'];
  user: AstroliftUser;
};

export type AstroliftApiTokenPlaintext = {
  apiToken: AstroliftApiToken;
  plaintext: Scalars['String']['output'];
};

export type AstroliftApiTokenPlaintextMutationResult = {
  data?: Maybe<AstroliftApiTokenPlaintext>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAppCertificate = {
  daysUntilExpiry: Scalars['Int']['output'];
  hostname: Scalars['String']['output'];
  id: Scalars['String']['output'];
  issuer: Scalars['String']['output'];
  notAfter: Scalars['String']['output'];
  renewalStatus: Scalars['String']['output'];
};

export type AstroliftAppDnsRecord = {
  name: Scalars['String']['output'];
  propagationStatus: Scalars['String']['output'];
  ttl: Scalars['Int']['output'];
  type: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type AstroliftAppDomain = {
  byoCertificateUploadedAt?: Maybe<Scalars['DateTime']['output']>;
  certState: Scalars['String']['output'];
  certificateState: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  expectedCnameTarget: Scalars['String']['output'];
  hostname: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  isPlatformManagedZone: Scalars['Boolean']['output'];
  lastCertificateError: Scalars['String']['output'];
  lastCheckedAt?: Maybe<Scalars['DateTime']['output']>;
  lastValidationError: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  requiredDnsRecords: Array<AstroliftAppDomainRequiredRecord>;
  txtChallengeToken: Scalars['String']['output'];
  validationMethod: Scalars['String']['output'];
  validationToken: Scalars['String']['output'];
};

export type AstroliftAppDomainMutationResult = {
  data?: Maybe<AstroliftAppDomain>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAppDomainRequiredRecord = {
  kind: Scalars['String']['output'];
  lastCheckedAt?: Maybe<Scalars['String']['output']>;
  message: Scalars['String']['output'];
  name: Scalars['String']['output'];
  propagated: Scalars['Boolean']['output'];
  ttl: Scalars['Int']['output'];
  value: Scalars['String']['output'];
};

export type AstroliftAppEnvironment = {
  clusterSlug?: Maybe<Scalars['String']['output']>;
  createdAt: Scalars['DateTime']['output'];
  deploysPaused: Scalars['Boolean']['output'];
  domainZone?: Maybe<Scalars['String']['output']>;
  id: Scalars['GUID']['output'];
  ingressPaused: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  requiredApprovals: Scalars['Int']['output'];
  url: Scalars['String']['output'];
};

export type AstroliftAppEnvironmentMutationResult = {
  data?: Maybe<AstroliftAppEnvironment>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAppGoldenSignal = {
  name: GoldenSignalKind;
  promql: Scalars['String']['output'];
  rangeSeconds: Scalars['Int']['output'];
  samples: Array<AstroliftTimeSeriesPoint>;
  unit: Scalars['String']['output'];
};

export type AstroliftAppHealthSummary = {
  appName: Scalars['String']['output'];
  appSlug: Scalars['String']['output'];
  environmentCount: Scalars['Int']['output'];
  hasRecentFailure: Scalars['Boolean']['output'];
  lastDeployedAt?: Maybe<Scalars['DateTime']['output']>;
  latestDeploymentStatus?: Maybe<Scalars['String']['output']>;
  latestImageTag: Scalars['String']['output'];
};

export type AstroliftAppIdentityBinding = {
  kind: Scalars['String']['output'];
  lastUsedAt?: Maybe<Scalars['String']['output']>;
  roleArnOrPrincipal: Scalars['String']['output'];
  trustPolicySummary: Scalars['String']['output'];
};

export type AstroliftAppLogLine = {
  container: Scalars['String']['output'];
  message: Scalars['String']['output'];
  podName: Scalars['String']['output'];
  stream: Scalars['String']['output'];
  timestamp: Scalars['DateTime']['output'];
};

export type AstroliftAppMetrics = {
  appSlug: Scalars['String']['output'];
  deployCount: Scalars['Int']['output'];
  errorRate: Scalars['Float']['output'];
  p50LatencyMs: Scalars['Float']['output'];
  p95LatencyMs: Scalars['Float']['output'];
  p99LatencyMs: Scalars['Float']['output'];
  requestRate: Scalars['Float']['output'];
  source: Scalars['String']['output'];
  timeRange: Scalars['String']['output'];
  timeSeries: Array<AstroliftAppMetricsPoint>;
};

export type AstroliftAppMetricsPoint = {
  errorRate: Scalars['Float']['output'];
  latencyP95: Scalars['Float']['output'];
  requestRate: Scalars['Float']['output'];
  timestamp: Scalars['DateTime']['output'];
};

export type AstroliftAppPod = {
  age?: Maybe<Scalars['DateTime']['output']>;
  containerStatuses: Array<AstroliftContainerStatus>;
  name: Scalars['String']['output'];
  node: Scalars['String']['output'];
  phase: Scalars['String']['output'];
  ready: Scalars['Boolean']['output'];
  restarts: Scalars['Int']['output'];
  status: Scalars['String']['output'];
  workload: Scalars['String']['output'];
};

export type AstroliftAppSecret = {
  bundleSlug: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  id: Scalars['String']['output'];
  isMasked: Scalars['Boolean']['output'];
  key: Scalars['String']['output'];
  lastEditedAt?: Maybe<Scalars['DateTime']['output']>;
  lastEditedBy?: Maybe<AstroliftSecretEditor>;
  managedServiceKind: Scalars['String']['output'];
  source: Scalars['String']['output'];
};

export type AstroliftAppSecretBundleAttachment = {
  attachedAt?: Maybe<Scalars['DateTime']['output']>;
  bundleName: Scalars['String']['output'];
  bundleSlug: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  keyCount: Scalars['Int']['output'];
  mergeOrder: Scalars['Int']['output'];
  prefix: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  teamSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftAppSecretBundleAttachmentMutationResult = {
  data?: Maybe<AstroliftAppSecretBundleAttachment>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAppSummary = {
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftAppTeamAccess = {
  accessLevel: Scalars['String']['output'];
  appId: Scalars['GUID']['output'];
  appSlug: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  isHome: Scalars['Boolean']['output'];
  teamId: Scalars['GUID']['output'];
  teamName: Scalars['String']['output'];
  teamSlug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftAppTeamAccessMutationResult = {
  data?: Maybe<AstroliftAppTeamAccess>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAuditEvent = {
  action: Scalars['String']['output'];
  actorDisplay: Scalars['String']['output'];
  actorId: Scalars['String']['output'];
  actorKind: Scalars['String']['output'];
  after?: Maybe<Scalars['JSON']['output']>;
  before?: Maybe<Scalars['JSON']['output']>;
  data: Scalars['JSON']['output'];
  decision: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  occurredAt: Scalars['DateTime']['output'];
  organizationId?: Maybe<Scalars['String']['output']>;
  requestId: Scalars['String']['output'];
  targetId: Scalars['String']['output'];
  targetKind: Scalars['String']['output'];
  targetSlug: Scalars['String']['output'];
};

export type AstroliftAuditEventPage = {
  items: Array<AstroliftAuditEvent>;
  nextCursor?: Maybe<Scalars['String']['output']>;
  totalCount?: Maybe<Scalars['Int']['output']>;
};

export type AstroliftAuditExport = {
  byteCount: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  downloadUrl: Scalars['String']['output'];
  expiresAt: Scalars['DateTime']['output'];
  format: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  rowCount: Scalars['Int']['output'];
  sha256: Scalars['String']['output'];
};

export type AstroliftAuditExportMutationResult = {
  data?: Maybe<AstroliftAuditExport>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAuditRetention = {
  days: Scalars['Int']['output'];
};

export type AstroliftBudget = {
  alertsAtPct: Array<Scalars['Int']['output']>;
  amountCents: Scalars['Int']['output'];
  currency: Scalars['String']['output'];
  currentSpendCents: Scalars['Int']['output'];
  id: Scalars['GUID']['output'];
  period: Scalars['String']['output'];
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
};

export type AstroliftBulkDeploymentResultData = {
  failedCount: Scalars['Int']['output'];
  results: Array<AstroliftBulkDeploymentResultItem>;
  succeededCount: Scalars['Int']['output'];
};

export type AstroliftBulkDeploymentResultDataMutationResult = {
  data?: Maybe<AstroliftBulkDeploymentResultData>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftBulkDeploymentResultItem = {
  deployment?: Maybe<AstroliftDeployment>;
  deploymentId: Scalars['GUID']['output'];
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftCapabilityDeprovisionPayload = {
  appId?: Maybe<Scalars['GUID']['output']>;
  clusterSlug: Scalars['String']['output'];
  detail: Scalars['String']['output'];
};

export type AstroliftCapabilityDeprovisionPayloadMutationResult = {
  data?: Maybe<AstroliftCapabilityDeprovisionPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftClusterBootstrapComponent = {
  defaultEnabled: Scalars['Boolean']['output'];
  helmValues: Scalars['JSON']['output'];
  key: Scalars['String']['output'];
  options: Array<AstroliftClusterBootstrapOption>;
  rationale: Scalars['String']['output'];
  requires: Array<Scalars['String']['output']>;
  title: Scalars['String']['output'];
};

export type AstroliftClusterBootstrapOption = {
  choices: Array<AstroliftClusterBootstrapOptionChoice>;
  default: Scalars['String']['output'];
  key: Scalars['String']['output'];
  label: Scalars['String']['output'];
};

export type AstroliftClusterBootstrapOptionChoice = {
  label: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type AstroliftClusterBootstrapPlan = {
  clusterId: Scalars['GUID']['output'];
  components: Array<AstroliftClusterBootstrapComponent>;
  providerPluginSlug: Scalars['String']['output'];
};

export type AstroliftClusterBootstrapRun = {
  chartVersion: Scalars['String']['output'];
  cliVersion: Scalars['String']['output'];
  endedAt: Scalars['DateTime']['output'];
  errorMessage: Scalars['String']['output'];
  hostInfo: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  installedReleases: Scalars['JSON']['output'];
  startedAt: Scalars['DateTime']['output'];
  status: Scalars['String']['output'];
  triggeredByUsername?: Maybe<Scalars['String']['output']>;
};

export type AstroliftClusterEvent = {
  count: Scalars['Int']['output'];
  firstSeen: Scalars['String']['output'];
  involvedObject: Scalars['String']['output'];
  lastSeen: Scalars['String']['output'];
  message: Scalars['String']['output'];
  name: Scalars['String']['output'];
  namespace: Scalars['String']['output'];
  reason: Scalars['String']['output'];
  type: Scalars['String']['output'];
};

export type AstroliftClusterHealth = {
  clusterId: Scalars['GUID']['output'];
  events: Array<AstroliftClusterEvent>;
  pods: Array<AstroliftClusterPodPhase>;
};

export type AstroliftClusterLifecycleAuditEntry = {
  actor?: Maybe<Scalars['String']['output']>;
  errors: Array<Scalars['String']['output']>;
  operation: Scalars['String']['output'];
  success: Scalars['Boolean']['output'];
  timestamp: Scalars['DateTime']['output'];
  variables: Scalars['JSON']['output'];
};

export type AstroliftClusterPodPhase = {
  count: Scalars['Int']['output'];
  namespace: Scalars['String']['output'];
  phase: Scalars['String']['output'];
};

export type AstroliftClusterWorkflowRun = {
  closedAt: Scalars['String']['output'];
  runId: Scalars['String']['output'];
  startedAt: Scalars['String']['output'];
  status: Scalars['String']['output'];
  workflowId: Scalars['String']['output'];
  workflowType: Scalars['String']['output'];
};

export type AstroliftClusterWorkloadHealth = {
  desiredReplicas: Scalars['Int']['output'];
  lastImageDeployedAt: Scalars['String']['output'];
  namespace: Scalars['String']['output'];
  readyReplicas: Scalars['Int']['output'];
  restartCount24h: Scalars['Int']['output'];
  workloadName: Scalars['String']['output'];
};

export type AstroliftCommandRun = {
  command: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  exitCode?: Maybe<Scalars['Int']['output']>;
  id: Scalars['GUID']['output'];
  invokedByUsername?: Maybe<Scalars['String']['output']>;
  logExcerpt: Scalars['String']['output'];
  /**
   * Last 200 lines of ``log_excerpt`` (#427). Mirrors the
   * ``ScheduledJobRun.output`` surface so the FE's shared row-expand
   * component works against both run kinds.
   */
  output: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  workloadSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftConnectUserSourceProviderPayload = {
  authorizationUrl: Scalars['String']['output'];
  providerConfigId: Scalars['GUID']['output'];
};

export type AstroliftConnectUserSourceProviderPayloadMutationResult = {
  data?: Maybe<AstroliftConnectUserSourceProviderPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftContainer = {
  args: Array<Scalars['String']['output']>;
  buildContext: Scalars['String']['output'];
  command: Array<Scalars['String']['output']>;
  dockerfilePath: Scalars['String']['output'];
  env: Scalars['JSON']['output'];
  healthcheckKind: Scalars['String']['output'];
  healthcheckPort?: Maybe<Scalars['Int']['output']>;
  healthcheckValue: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  imageRef: Scalars['String']['output'];
  isPrimary: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  port: Scalars['Int']['output'];
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftContainerResources = {
  cpuLimit: Scalars['String']['output'];
  cpuRequest: Scalars['String']['output'];
  memoryLimit: Scalars['String']['output'];
  memoryRequest: Scalars['String']['output'];
};

export type AstroliftContainerStatus = {
  image: Scalars['String']['output'];
  kind: Scalars['String']['output'];
  lastRestartAt?: Maybe<Scalars['DateTime']['output']>;
  lastRestartReasons: Array<Scalars['String']['output']>;
  name: Scalars['String']['output'];
  ready: Scalars['Boolean']['output'];
  resources: AstroliftContainerResources;
  restarts: Scalars['Int']['output'];
  state: Scalars['String']['output'];
  terminatedReason: Scalars['String']['output'];
  waitingReason: Scalars['String']['output'];
};

export type AstroliftCostAttribution = {
  attributedRows: Array<AstroliftCostBindingRow>;
  currency: Scalars['String']['output'];
  totalCents: Scalars['Int']['output'];
  unattributedCents: Scalars['Int']['output'];
};

export type AstroliftCostBindingRow = {
  amountCents: Scalars['Int']['output'];
  by: Scalars['String']['output'];
  currency: Scalars['String']['output'];
  managedServiceBindingId?: Maybe<Scalars['String']['output']>;
  managedServiceId?: Maybe<Scalars['String']['output']>;
  managedServiceKind?: Maybe<Scalars['String']['output']>;
  managedServiceName?: Maybe<Scalars['String']['output']>;
  registeredAppSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftCostForecast = {
  confidence: ForecastConfidence;
  currency: Scalars['String']['output'];
  deltaPct: Scalars['Float']['output'];
  mtdCents: Scalars['Int']['output'];
  previousMonthCents: Scalars['Int']['output'];
  projectedMonthlyCents: Scalars['Int']['output'];
};

export type AstroliftCostSnapshot = {
  amountCents: Scalars['Int']['output'];
  by: Scalars['String']['output'];
  currency: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  managedServiceBindingId?: Maybe<Scalars['String']['output']>;
  projectId?: Maybe<Scalars['String']['output']>;
  registeredAppId?: Maybe<Scalars['String']['output']>;
  source: Scalars['String']['output'];
  takenAt: Scalars['Date']['output'];
};

export type AstroliftCostTrendPoint = {
  amountCents: Scalars['Int']['output'];
  currency: Scalars['String']['output'];
  date: Scalars['Date']['output'];
  isAnomaly: Scalars['Boolean']['output'];
};

export type AstroliftDeployToken = {
  createdAt: Scalars['DateTime']['output'];
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  isRevoked: Scalars['Boolean']['output'];
  last4: Scalars['String']['output'];
  lastRotatedAt?: Maybe<Scalars['DateTime']['output']>;
  lastUsedAt?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  scopes: Array<Scalars['String']['output']>;
};

export type AstroliftDeployment = {
  abortedReason: Scalars['String']['output'];
  approvalsReceived: Scalars['Int']['output'];
  approvalsRequired: Scalars['Int']['output'];
  approvedBy: Array<AstroliftDeploymentApprover>;
  awaitingApprovers: Array<AstroliftDeploymentApprover>;
  branch: Scalars['String']['output'];
  ciActorKind: Scalars['String']['output'];
  ciProvider: Scalars['String']['output'];
  ciRunUrl: Scalars['String']['output'];
  clusterRevision: Scalars['String']['output'];
  commitAuthor: Scalars['String']['output'];
  commitMessage: Scalars['String']['output'];
  commitSha: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  durationSeconds?: Maybe<Scalars['Int']['output']>;
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  environmentName: Scalars['String']['output'];
  failedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  imageDigest: Scalars['String']['output'];
  imageTag: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  repoUrl: Scalars['String']['output'];
  requiredApproverCount: Scalars['Int']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  succeededAt?: Maybe<Scalars['DateTime']['output']>;
  triggerKind: Scalars['String']['output'];
  triggeredByMe: Scalars['Boolean']['output'];
  triggeredByUserId?: Maybe<Scalars['String']['output']>;
  workloadSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftDeploymentApprovalHistoryEntry = {
  action: Scalars['String']['output'];
  actorDisplay: Scalars['String']['output'];
  actorId: Scalars['String']['output'];
  actorKind: Scalars['String']['output'];
  decision: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  occurredAt: Scalars['DateTime']['output'];
  reason: Scalars['String']['output'];
};

export type AstroliftDeploymentApprover = {
  approvedAt?: Maybe<Scalars['DateTime']['output']>;
  displayName: Scalars['String']['output'];
  email: Scalars['String']['output'];
  mailtoUrl: Scalars['String']['output'];
  userId: Scalars['String']['output'];
};

export type AstroliftDeploymentLifecycleEvent = {
  deploymentId: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  occurredAt: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftDeploymentLogEntry = {
  deploymentId: Scalars['String']['output'];
  detail: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  message: Scalars['String']['output'];
  occurredAt: Scalars['DateTime']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftDeploymentMetrics = {
  failed: Scalars['Int']['output'];
  inFlight: Scalars['Int']['output'];
  meanDurationSeconds?: Maybe<Scalars['Float']['output']>;
  p95DurationSeconds?: Maybe<Scalars['Float']['output']>;
  rolledBack: Scalars['Int']['output'];
  succeeded: Scalars['Int']['output'];
  successRate: Scalars['Float']['output'];
  total: Scalars['Int']['output'];
  windowDays: Scalars['Int']['output'];
};

export type AstroliftDeploymentMutationResult = {
  data?: Maybe<AstroliftDeployment>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftDeregisterAppPayload = {
  stillLiveResources: Array<Scalars['String']['output']>;
  workflowId: Scalars['String']['output'];
};

export type AstroliftDeregisterAppPayloadMutationResult = {
  data?: Maybe<AstroliftDeregisterAppPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftDisconnectUserSourceProviderPayload = {
  disconnectedId?: Maybe<Scalars['GUID']['output']>;
  providerConfigId: Scalars['GUID']['output'];
};

export type AstroliftDisconnectUserSourceProviderPayloadMutationResult = {
  data?: Maybe<AstroliftDisconnectUserSourceProviderPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftEvent = {
  eventType: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  occurredAt: Scalars['DateTime']['output'];
  organizationId?: Maybe<Scalars['String']['output']>;
  payload: Scalars['JSON']['output'];
  projectId?: Maybe<Scalars['String']['output']>;
  registeredAppId?: Maybe<Scalars['String']['output']>;
  teamId?: Maybe<Scalars['String']['output']>;
};

export type AstroliftEventPage = {
  items: Array<AstroliftEvent>;
  nextCursor?: Maybe<Scalars['String']['output']>;
};

export type AstroliftForceRedeployPayload = {
  deploymentsCancelled: Scalars['Int']['output'];
  dispatchMessage?: Maybe<Scalars['String']['output']>;
  k8sObjectsDeleted: Scalars['Int']['output'];
  runUrl?: Maybe<Scalars['String']['output']>;
  workflowDispatched: Scalars['Boolean']['output'];
};

export type AstroliftForceRedeployPayloadMutationResult = {
  data?: Maybe<AstroliftForceRedeployPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftIdentityProvider = {
  clientId: Scalars['String']['output'];
  config: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  isDefault: Scalars['Boolean']['output'];
  kind: Scalars['String']['output'];
  metadataUrl: Scalars['String']['output'];
  name: Scalars['String']['output'];
  oidcDiscoveryUrl: Scalars['String']['output'];
  organizationSlug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftIdentityProviderMutationResult = {
  data?: Maybe<AstroliftIdentityProvider>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftInstallSourceWebhookPayload = {
  hookId: Scalars['String']['output'];
  receiverUrl: Scalars['String']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftInstallSourceWebhookPayloadMutationResult = {
  data?: Maybe<AstroliftInstallSourceWebhookPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftInvitation = {
  acceptedAt?: Maybe<Scalars['DateTime']['output']>;
  createdAt: Scalars['DateTime']['output'];
  email: Scalars['String']['output'];
  expiresAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  invitedByUsername?: Maybe<Scalars['String']['output']>;
  roleSlug?: Maybe<Scalars['String']['output']>;
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftInvitationCreated = {
  acceptUrlPath: Scalars['String']['output'];
  invitation: AstroliftInvitation;
  plaintextToken: Scalars['String']['output'];
};

export type AstroliftInvitationCreatedMutationResult = {
  data?: Maybe<AstroliftInvitationCreated>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftInvitationMutationResult = {
  data?: Maybe<AstroliftInvitation>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftLogoutAllSessionsPayload = {
  keptCurrent: Scalars['Boolean']['output'];
  revokedCount: Scalars['Int']['output'];
};

export type AstroliftLogoutAllSessionsPayloadMutationResult = {
  data?: Maybe<AstroliftLogoutAllSessionsPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftManagedDomain = {
  createdAt: Scalars['DateTime']['output'];
  defaultFor: Scalars['String']['output'];
  dnsDriver: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isWildcardManaged: Scalars['Boolean']['output'];
  organizationSlug?: Maybe<Scalars['String']['output']>;
  zone: Scalars['String']['output'];
};

export type AstroliftManagedDomainMutationResult = {
  data?: Maybe<AstroliftManagedDomain>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftManagedService = {
  config: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  name: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  status: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
  variant: Scalars['String']['output'];
};

export type AstroliftManagedServiceMutationResult = {
  data?: Maybe<AstroliftManagedService>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftMember = {
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  joinedAt?: Maybe<Scalars['DateTime']['output']>;
  lastSeenAt?: Maybe<Scalars['DateTime']['output']>;
  lifecycle: Scalars['String']['output'];
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
  user: AstroliftUser;
};

export type AstroliftMyConnectedAccount = {
  expiresAt?: Maybe<Scalars['String']['output']>;
  isConnected: Scalars['Boolean']['output'];
  lastUsedAt?: Maybe<Scalars['String']['output']>;
  linkedAccountLogin?: Maybe<Scalars['String']['output']>;
  providerConfigId: Scalars['GUID']['output'];
  providerKind: Scalars['String']['output'];
  providerLabel: Scalars['String']['output'];
  reauthRequired: Scalars['Boolean']['output'];
};

export type AstroliftMyProfile = {
  email: Scalars['String']['output'];
  firstName: Scalars['String']['output'];
  lastName: Scalars['String']['output'];
  lockedFields: Array<Scalars['String']['output']>;
  orgAllowsEdit: Scalars['Boolean']['output'];
  userId: Scalars['Int']['output'];
  username: Scalars['String']['output'];
};

export type AstroliftMyProfileMutationResult = {
  data?: Maybe<AstroliftMyProfile>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftNavTree = {
  organization: AstroliftOrganization;
  teams: Array<AstroliftNavTreeTeam>;
  unassignedApps: Array<AstroliftAppSummary>;
};

export type AstroliftNavTreeProject = {
  apps: Array<AstroliftAppSummary>;
  project: AstroliftProject;
};

export type AstroliftNavTreeTeam = {
  projects: Array<AstroliftNavTreeProject>;
  team: AstroliftTeam;
  unassignedApps: Array<AstroliftAppSummary>;
};

export type AstroliftNotification = {
  body: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  link: Scalars['String']['output'];
  readAt?: Maybe<Scalars['DateTime']['output']>;
  title: Scalars['String']['output'];
  userId: Scalars['String']['output'];
};

export type AstroliftNotificationMutationResult = {
  data?: Maybe<AstroliftNotification>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftOrganization = {
  allowUserProfileEdit: Scalars['Boolean']['output'];
  auditLogRetentionDays: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  logRetentionDaysDefault: Scalars['Int']['output'];
  name: Scalars['String']['output'];
  previewMaxActiveDefault: Scalars['Int']['output'];
  scimEnabled: Scalars['Boolean']['output'];
  slug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
  website: Scalars['String']['output'];
};

export type AstroliftOrganizationAllowlistedDomain = {
  createdAt: Scalars['DateTime']['output'];
  defaultRoleSlug?: Maybe<Scalars['String']['output']>;
  domain: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  requiresReview: Scalars['Boolean']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftOrganizationAllowlistedDomainMutationResult = {
  data?: Maybe<AstroliftOrganizationAllowlistedDomain>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftOrganizationMutationResult = {
  data?: Maybe<AstroliftOrganization>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftPolicy = {
  actionPattern: Scalars['String']['output'];
  actorPattern: Scalars['JSON']['output'];
  conditions: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  description: Scalars['String']['output'];
  effect: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  resourcePattern: Scalars['JSON']['output'];
  scopeId?: Maybe<Scalars['String']['output']>;
  scopeLevel: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftPolicyMutationResult = {
  data?: Maybe<AstroliftPolicy>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftPreviewAggregateResources = {
  cpuCores: Scalars['Float']['output'];
  memoryBytes: Scalars['Float']['output'];
  podCount: Scalars['Int']['output'];
};

export type AstroliftPreviewEnvironment = {
  aggregateResources: AstroliftPreviewAggregateResources;
  branch: Scalars['String']['output'];
  commitSha: Scalars['String']['output'];
  estimatedDailyCostUsd?: Maybe<Scalars['Float']['output']>;
  hostname: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  lastDeployedAt?: Maybe<Scalars['DateTime']['output']>;
  namespace: Scalars['String']['output'];
  prNumber: Scalars['Int']['output'];
  prUrl: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  sourceUrl: Scalars['String']['output'];
  status: Scalars['String']['output'];
  tornDownAt?: Maybe<Scalars['DateTime']['output']>;
  ttlUntil: Scalars['DateTime']['output'];
};

export type AstroliftPreviewEnvironmentMutationResult = {
  data?: Maybe<AstroliftPreviewEnvironment>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftProject = {
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  organization: AstroliftOrganization;
  slug: Scalars['String']['output'];
  team: AstroliftTeam;
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftProjectMutationResult = {
  data?: Maybe<AstroliftProject>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftProviderPlugin = {
  capabilitiesManifest: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  isEnabled: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  version: Scalars['String']['output'];
};

export type AstroliftPushCiSecretsPayload = {
  repo: Scalars['String']['output'];
  rotatedTokenLast4: Scalars['String']['output'];
  secretNames: Array<Scalars['String']['output']>;
};

export type AstroliftPushCiSecretsPayloadMutationResult = {
  data?: Maybe<AstroliftPushCiSecretsPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftPushCiWorkflowPayload = {
  commitSha?: Maybe<Scalars['String']['output']>;
  prUrl?: Maybe<Scalars['String']['output']>;
  status: Scalars['String']['output'];
};

export type AstroliftPushCiWorkflowPayloadMutationResult = {
  data?: Maybe<AstroliftPushCiWorkflowPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftQuota = {
  currentUsage: Scalars['Float']['output'];
  hardLimit: Scalars['Float']['output'];
  id: Scalars['GUID']['output'];
  resource: Scalars['String']['output'];
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
  softLimit: Scalars['Float']['output'];
};

export type AstroliftRegisteredApp = {
  approverTeamId?: Maybe<Scalars['GUID']['output']>;
  approverUserIds: Array<Scalars['String']['output']>;
  createdAt: Scalars['DateTime']['output'];
  cronExpression: Scalars['String']['output'];
  cronPaused: Scalars['Boolean']['output'];
  defaultBranch: Scalars['String']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  deployBranch: Scalars['String']['output'];
  deployTokenLast4: Scalars['String']['output'];
  description: Scalars['String']['output'];
  ecrPushRoleArn: Scalars['String']['output'];
  ecrRepoUri: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  k8sNamespace: Scalars['String']['output'];
  lastResyncAt?: Maybe<Scalars['DateTime']['output']>;
  lastSyncedHash: Scalars['String']['output'];
  logRetentionDays: Scalars['Int']['output'];
  manifestHash: Scalars['String']['output'];
  manifestPath: Scalars['String']['output'];
  manifestSyncState: Scalars['String']['output'];
  minimumApprovals: Scalars['Int']['output'];
  name: Scalars['String']['output'];
  organizationSlug: Scalars['String']['output'];
  previewEnabled: Scalars['Boolean']['output'];
  previewMaxActive: Scalars['Int']['output'];
  previewScreenshotUrl: Scalars['String']['output'];
  projectId?: Maybe<Scalars['GUID']['output']>;
  projectName: Scalars['String']['output'];
  projectSlug: Scalars['String']['output'];
  provisioningError: Scalars['String']['output'];
  provisioningStatus: Scalars['String']['output'];
  rawManifest: Scalars['String']['output'];
  rawManifestStaged: Scalars['String']['output'];
  registryRepoUri: Scalars['String']['output'];
  requiresApproval: Scalars['Boolean']['output'];
  securityPolicy: AstroliftSecurityPolicy;
  slug: Scalars['String']['output'];
  sourceKind: Scalars['String']['output'];
  sourceRepo: Scalars['String']['output'];
  sourceUrl: Scalars['String']['output'];
  sourceWebhookInstalledAt?: Maybe<Scalars['DateTime']['output']>;
  subdomain: Scalars['String']['output'];
  teamId?: Maybe<Scalars['GUID']['output']>;
  teamName: Scalars['String']['output'];
  teamSlug: Scalars['String']['output'];
  triggerMode: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftRegisteredAppMutationResult = {
  data?: Maybe<AstroliftRegisteredApp>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRemoteRepo = {
  cloneUrlHttps: Scalars['String']['output'];
  cloneUrlSsh: Scalars['String']['output'];
  defaultBranch: Scalars['String']['output'];
  description: Scalars['String']['output'];
  fullName: Scalars['String']['output'];
  isArchived: Scalars['Boolean']['output'];
  isFork: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  pushedAt?: Maybe<Scalars['String']['output']>;
  visibility: Scalars['String']['output'];
  webUrl: Scalars['String']['output'];
};

export type AstroliftRemoteRepoList = {
  errorCode?: Maybe<Scalars['String']['output']>;
  errorMessage?: Maybe<Scalars['String']['output']>;
  recoverable: Scalars['Boolean']['output'];
  repos: Array<AstroliftRemoteRepo>;
};

export type AstroliftRenderedManifest = {
  appSlug: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  error?: Maybe<Scalars['String']['output']>;
  errorColumn?: Maybe<Scalars['Int']['output']>;
  errorLine?: Maybe<Scalars['Int']['output']>;
  errorPath?: Maybe<Scalars['String']['output']>;
  imageTag: Scalars['String']['output'];
  namespace: Scalars['String']['output'];
  resources: Scalars['JSON']['output'];
};

export type AstroliftRevealedSecret = {
  environmentName: Scalars['String']['output'];
  key: Scalars['String']['output'];
  revealedAt: Scalars['DateTime']['output'];
  secretId: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type AstroliftRevealedSecretMutationResult = {
  data?: Maybe<AstroliftRevealedSecret>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRole = {
  description: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isSystem: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  permissions: Array<Scalars['String']['output']>;
  scopeLevel: Scalars['String']['output'];
  slug: Scalars['String']['output'];
};

export type AstroliftRoleBinding = {
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  grantedAt: Scalars['DateTime']['output'];
  groupExternalId: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  inherits: Scalars['Boolean']['output'];
  role: AstroliftRole;
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
  user?: Maybe<AstroliftUser>;
};

export type AstroliftRoleBindingMutationResult = {
  data?: Maybe<AstroliftRoleBinding>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRoleMutationResult = {
  data?: Maybe<AstroliftRole>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRunJobOncePayload = {
  logsUrl?: Maybe<Scalars['String']['output']>;
  namespace: Scalars['String']['output'];
  runName: Scalars['String']['output'];
};

export type AstroliftRunJobOncePayloadMutationResult = {
  data?: Maybe<AstroliftRunJobOncePayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftScheduledJobRun = {
  createdAt: Scalars['DateTime']['output'];
  durationSeconds?: Maybe<Scalars['Int']['output']>;
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  environmentName: Scalars['String']['output'];
  exitCode?: Maybe<Scalars['Int']['output']>;
  id: Scalars['GUID']['output'];
  k8sJobName: Scalars['String']['output'];
  logExcerpt: Scalars['String']['output'];
  /**
   * Last 200 lines of ``log_excerpt`` (#427). Powers the inline
   * row-expand surface on the jobs table so operators can confirm a
   * run worked without leaving the page. The full tail lives behind
   * the per-app logs surface; the UI footer flags truncation.
   */
  output: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftScmPushCiWorkflowResult = {
  commitSha: Scalars['String']['output'];
  filePath: Scalars['String']['output'];
  repoUrl: Scalars['String']['output'];
};

export type AstroliftScmPushCiWorkflowResultMutationResult = {
  data?: Maybe<AstroliftScmPushCiWorkflowResult>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftScmWebhookInstallation = {
  createdAt: Scalars['DateTime']['output'];
  hookId: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  providerShortCircuited: Scalars['Boolean']['output'];
  repoFullName: Scalars['String']['output'];
  sourceConnectionId: Scalars['GUID']['output'];
  webhookUrl: Scalars['String']['output'];
};

export type AstroliftScmWebhookInstallationMutationResult = {
  data?: Maybe<AstroliftScmWebhookInstallation>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftScmWebhookSecretReveal = {
  connectionId: Scalars['GUID']['output'];
  plaintextSecret: Scalars['String']['output'];
  webhookUrlPath: Scalars['String']['output'];
};

export type AstroliftScmWebhookSecretRevealMutationResult = {
  data?: Maybe<AstroliftScmWebhookSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftSecretBundle = {
  backendRef: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  organizationSlug: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  teamSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftSecretBundleMutationResult = {
  data?: Maybe<AstroliftSecretBundle>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftSecretEditor = {
  displayName: Scalars['String']['output'];
  id: Scalars['String']['output'];
  username: Scalars['String']['output'];
};

export type AstroliftSecurityPolicy = {
  blockOnCriticalCves: Scalars['Boolean']['output'];
  blockOnHighCveThreshold?: Maybe<Scalars['Int']['output']>;
  blockOnMissingSignature: Scalars['Boolean']['output'];
};

export type AstroliftSourceConnection = {
  accountLogin: Scalars['String']['output'];
  apiBaseUrl: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  displayName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  installationId: Scalars['String']['output'];
  isActive: Scalars['Boolean']['output'];
  isOauthAppConfig: Scalars['Boolean']['output'];
  isPersonal: Scalars['Boolean']['output'];
  kind: Scalars['String']['output'];
  lastUsedAt?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  oauthClientId: Scalars['String']['output'];
  oauthRedirectUri: Scalars['String']['output'];
  parentOauthAppId?: Maybe<Scalars['GUID']['output']>;
  repoVisibilityScopes: Array<Scalars['String']['output']>;
  tokenExpiresAt?: Maybe<Scalars['DateTime']['output']>;
  updatedAt: Scalars['DateTime']['output'];
  userUsername?: Maybe<Scalars['String']['output']>;
};

export type AstroliftSourceConnectionMutationResult = {
  data?: Maybe<AstroliftSourceConnection>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftSourceFile = {
  content?: Maybe<Scalars['String']['output']>;
  errorCode?: Maybe<Scalars['String']['output']>;
  errorMessage?: Maybe<Scalars['String']['output']>;
  path: Scalars['String']['output'];
  recoverable: Scalars['Boolean']['output'];
  ref: Scalars['String']['output'];
  repoFullName: Scalars['String']['output'];
};

export type AstroliftSshDeployKey = {
  createdAt: Scalars['DateTime']['output'];
  fingerprintSha256: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  lastUsedAt?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  publicKey: Scalars['String']['output'];
  registeredAppSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftSshDeployKeyCreated = {
  key: AstroliftSshDeployKey;
};

export type AstroliftSshDeployKeyCreatedMutationResult = {
  data?: Maybe<AstroliftSshDeployKeyCreated>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftSshDeployKeyMutationResult = {
  data?: Maybe<AstroliftSshDeployKey>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftStatusCodeBreakdown = {
  promql: Scalars['String']['output'];
  rangeSeconds: Scalars['Int']['output'];
  series: Array<AstroliftStatusCodeSeries>;
};

export type AstroliftStatusCodeSeries = {
  codeClass: Scalars['String']['output'];
  samples: Array<AstroliftTimeSeriesPoint>;
  topCodes: Array<Scalars['String']['output']>;
};

export type AstroliftTeam = {
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  organization: AstroliftOrganization;
  slug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftTeamMutationResult = {
  data?: Maybe<AstroliftTeam>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftTenantCluster = {
  authMethod: Scalars['String']['output'];
  bootstrapRuns: Array<AstroliftClusterBootstrapRun>;
  capabilities: Scalars['JSON']['output'];
  capabilitiesProbedAt?: Maybe<Scalars['DateTime']['output']>;
  createdAt: Scalars['DateTime']['output'];
  endpoint: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  ingressClass: Scalars['String']['output'];
  isActive: Scalars['Boolean']['output'];
  lastBootstrapRun?: Maybe<AstroliftClusterBootstrapRun>;
  lastManagementError: Scalars['String']['output'];
  lifecycle: Scalars['String']['output'];
  managedAt?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  organizationSlug?: Maybe<Scalars['String']['output']>;
  providerPluginSlug: Scalars['String']['output'];
  region: Scalars['String']['output'];
  secretsBackendProvisionedAt?: Maybe<Scalars['DateTime']['output']>;
  slug: Scalars['String']['output'];
};


export type AstroliftTenantClusterBootstrapRunsArgs = {
  limit?: Scalars['Int']['input'];
};

export type AstroliftTenantClusterMutationResult = {
  data?: Maybe<AstroliftTenantCluster>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftTimeSeriesPoint = {
  ts: Scalars['DateTime']['output'];
  value: Scalars['Float']['output'];
};

export type AstroliftTriggerDeployWorkflowPayload = {
  dispatchedBranch: Scalars['String']['output'];
  runUrl: Scalars['String']['output'];
};

export type AstroliftTriggerDeployWorkflowPayloadMutationResult = {
  data?: Maybe<AstroliftTriggerDeployWorkflowPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftUser = {
  email: Scalars['String']['output'];
  id: Scalars['String']['output'];
  isActive: Scalars['Boolean']['output'];
  username: Scalars['String']['output'];
};

export type AstroliftWebhookDelivery = {
  deliveredAt: Scalars['DateTime']['output'];
  deliveryId: Scalars['String']['output'];
  error: Scalars['String']['output'];
  eventType: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isTest: Scalars['Boolean']['output'];
  latencyMs: Scalars['Int']['output'];
  requestPayloadExcerpt: Scalars['String']['output'];
  responseBodyExcerpt: Scalars['String']['output'];
  retryAttempt: Scalars['Int']['output'];
  statusCode?: Maybe<Scalars['Int']['output']>;
  subscriptionId: Scalars['GUID']['output'];
  success: Scalars['Boolean']['output'];
};

export type AstroliftWebhookSubscription = {
  createdAt: Scalars['DateTime']['output'];
  events: Array<Scalars['String']['output']>;
  failureCount: Scalars['Int']['output'];
  format: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  lastDeliveryAt?: Maybe<Scalars['DateTime']['output']>;
  lastResponseStatus?: Maybe<Scalars['Int']['output']>;
  secretRotatedAt?: Maybe<Scalars['DateTime']['output']>;
  url: Scalars['String']['output'];
};

export type AstroliftWebhookSubscriptionMutationResult = {
  data?: Maybe<AstroliftWebhookSubscription>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftWebhookTestResult = {
  delivered: Scalars['Boolean']['output'];
  deliveryId: Scalars['String']['output'];
  durationMs: Scalars['Int']['output'];
  error: Scalars['String']['output'];
  responseBodyExcerpt: Scalars['String']['output'];
  statusCode?: Maybe<Scalars['Int']['output']>;
  subscriptionId: Scalars['GUID']['output'];
  timestamp: Scalars['DateTime']['output'];
  url: Scalars['String']['output'];
};

export type AstroliftWebhookTestResultMutationResult = {
  data?: Maybe<AstroliftWebhookTestResult>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftWorkflowRun = {
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  failure: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  organizationId?: Maybe<Scalars['String']['output']>;
  registeredAppId?: Maybe<Scalars['String']['output']>;
  runId: Scalars['String']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  workflowId: Scalars['String']['output'];
  workflowKind: Scalars['String']['output'];
};

export type AstroliftWorkload = {
  /**
   * CronJob concurrency policy (#427): ``forbid`` | ``queue`` |
   * ``replace``. Only meaningful when ``kind == "cronjob"``; always
   * present so the FE doesn't have to branch on null. Defaults to
   * ``forbid`` for non-cronjob rows so the badge component never
   * renders garbage.
   */
  concurrencyPolicy: Scalars['String']['output'];
  cpuLimit: Scalars['String']['output'];
  cpuRequest: Scalars['String']['output'];
  hpaMaxReplicas?: Maybe<Scalars['Int']['output']>;
  hpaMinReplicas?: Maybe<Scalars['Int']['output']>;
  hpaTargetCpuPct: Scalars['Int']['output'];
  id: Scalars['GUID']['output'];
  inClusterServiceFqdn: Scalars['String']['output'];
  isPublic: Scalars['Boolean']['output'];
  kind: Scalars['String']['output'];
  memoryLimit: Scalars['String']['output'];
  memoryRequest: Scalars['String']['output'];
  name: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  replicas: Scalars['Int']['output'];
  schedule: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  storageClass: Scalars['String']['output'];
  storageSize: Scalars['String']['output'];
};

export type AstroliftWorkloadOpPayload = {
  desiredReplicas?: Maybe<Scalars['Int']['output']>;
  newRevision?: Maybe<Scalars['Int']['output']>;
  readyReplicas?: Maybe<Scalars['Int']['output']>;
  workloadId: Scalars['GUID']['output'];
};

export type AstroliftWorkloadOpPayloadMutationResult = {
  data?: Maybe<AstroliftWorkloadOpPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftWorkloadPodStatusBucket = {
  count: Scalars['Int']['output'];
  percent: Scalars['Float']['output'];
  pods: Array<AstroliftWorkloadPodSummary>;
  status: Scalars['String']['output'];
};

export type AstroliftWorkloadPodSummary = {
  age?: Maybe<Scalars['DateTime']['output']>;
  name: Scalars['String']['output'];
  ready: Scalars['Boolean']['output'];
};

export type AttachSecretBundleInput = {
  appSlug: Scalars['String']['input'];
  bundleSlug: Scalars['String']['input'];
  environmentName: Scalars['String']['input'];
  prefix: InputMaybe<Scalars['String']['input']>;
};

export type Attachmentremovedpayload = {
  attachmentId: Scalars['GUID']['output'];
  deleted: Scalars['Boolean']['output'];
};

export type AttachmentremovedpayloadMutationResult = {
  data?: Maybe<Attachmentremovedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AuditLogEntry = {
  errors: Array<Scalars['String']['output']>;
  ipAddress?: Maybe<Scalars['String']['output']>;
  operation: Scalars['String']['output'];
  success: Scalars['Boolean']['output'];
  timestamp: Scalars['DateTime']['output'];
  username?: Maybe<Scalars['String']['output']>;
  variables: Scalars['JSON']['output'];
};

export type AvailableTransition = {
  conditionsMet: Scalars['Boolean']['output'];
  fromState: Scalars['String']['output'];
  label: Scalars['String']['output'];
  toState: Scalars['String']['output'];
};

export type BootstrapOptionOverride = {
  componentKey: Scalars['String']['input'];
  optionKey: Scalars['String']['input'];
  value: Scalars['String']['input'];
};

export type Bootstraprunrecordedpayload = {
  id: Scalars['GUID']['output'];
};

export type BootstraprunrecordedpayloadMutationResult = {
  data?: Maybe<Bootstraprunrecordedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type BringClusterIntoManagementInputType = {
  clusterId: Scalars['GUID']['input'];
};

export type BulkApproveDeploymentsInput = {
  deploymentIds: Array<Scalars['GUID']['input']>;
  reason: InputMaybe<Scalars['String']['input']>;
};

export type BulkImportAppSecretsInput = {
  appSlug: Scalars['String']['input'];
  dotenvText: Scalars['String']['input'];
};

export type BulkRejectDeploymentsInput = {
  deploymentIds: Array<Scalars['GUID']['input']>;
  reason: Scalars['String']['input'];
};

export type Bulkimportpayload = {
  appSlug: Scalars['String']['output'];
  keysSet: Array<Scalars['String']['output']>;
  rawManifestStaged: Scalars['String']['output'];
};

export type BulkimportpayloadMutationResult = {
  data?: Maybe<Bulkimportpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type ConfigureProviderPluginInput = {
  config: Scalars['JSON']['input'];
  organizationScoped: Scalars['Boolean']['input'];
  pluginSlug: Scalars['String']['input'];
};

export type ConfirmUploadResult = {
  ack: Scalars['Boolean']['output'];
};

export type ConnectSourceInput = {
  accountLogin: InputMaybe<Scalars['String']['input']>;
  apiBaseUrl: InputMaybe<Scalars['String']['input']>;
  displayName: InputMaybe<Scalars['String']['input']>;
  installationId: InputMaybe<Scalars['String']['input']>;
  kind: Scalars['String']['input'];
  oauthClientId: InputMaybe<Scalars['String']['input']>;
  oauthRedirectUri: InputMaybe<Scalars['String']['input']>;
  repoVisibilityScopes: InputMaybe<Array<Scalars['String']['input']>>;
  secretPlaintext: Scalars['String']['input'];
};

export type ConnectUserSourceProviderInput = {
  providerConfigId: Scalars['GUID']['input'];
};

export type CostWindow =
  | 'D7'
  | 'D30'
  | 'H24'
  | 'MTD';

export type CreateAlertRuleInput = {
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  name: Scalars['String']['input'];
  notifyChannels: InputMaybe<Scalars['JSON']['input']>;
  predicate: InputMaybe<Scalars['JSON']['input']>;
  severity: InputMaybe<Scalars['String']['input']>;
  target: Scalars['String']['input'];
  targetId: InputMaybe<Scalars['String']['input']>;
};

export type CreateApiTokenInput = {
  expiresInDays: InputMaybe<Scalars['Int']['input']>;
  name: Scalars['String']['input'];
  scopes: InputMaybe<Array<Scalars['String']['input']>>;
  teamSlug: InputMaybe<Scalars['String']['input']>;
};

export type CreateDeployTokenInput = {
  appSlug: Scalars['String']['input'];
  expiresAtIso: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
  scopes: InputMaybe<Array<Scalars['String']['input']>>;
};

export type CreateIdentityProviderInput = {
  clientId: InputMaybe<Scalars['String']['input']>;
  clientSecretRef: InputMaybe<Scalars['String']['input']>;
  config: InputMaybe<Scalars['JSON']['input']>;
  displayName: InputMaybe<Scalars['String']['input']>;
  kind: Scalars['String']['input'];
  metadataUrl: InputMaybe<Scalars['String']['input']>;
  oidcDiscoveryUrl: InputMaybe<Scalars['String']['input']>;
  setActive: Scalars['Boolean']['input'];
};

export type CreateInvitationInput = {
  email: Scalars['String']['input'];
  expiresInDays: InputMaybe<Scalars['Int']['input']>;
  roleSlug: InputMaybe<Scalars['String']['input']>;
};

export type CreateManagedDomainInput = {
  defaultFor: Scalars['String']['input'];
  dnsConfig: InputMaybe<Scalars['JSON']['input']>;
  dnsDriver: Scalars['String']['input'];
  isWildcardManaged: Scalars['Boolean']['input'];
  organizationScoped: Scalars['Boolean']['input'];
  zone: Scalars['String']['input'];
};

export type CreateOrganizationInput = {
  name: Scalars['String']['input'];
  slug: Scalars['String']['input'];
  website: InputMaybe<Scalars['String']['input']>;
};

export type CreatePolicyInput = {
  actionPattern: Scalars['String']['input'];
  actorPattern: InputMaybe<Scalars['JSON']['input']>;
  conditions: InputMaybe<Scalars['JSON']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  effect: Scalars['String']['input'];
  name: Scalars['String']['input'];
  resourcePattern: InputMaybe<Scalars['JSON']['input']>;
  scopeId: InputMaybe<Scalars['Int']['input']>;
  scopeLevel: Scalars['String']['input'];
  slug: Scalars['String']['input'];
};

export type CreateProjectInput = {
  description: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
  slug: Scalars['String']['input'];
  teamId: Scalars['GUID']['input'];
};

export type CreateRoleInput = {
  description: Scalars['String']['input'];
  name: Scalars['String']['input'];
  permissions: Array<Scalars['String']['input']>;
  scopeLevel: Scalars['String']['input'];
  slug: Scalars['String']['input'];
};

export type CreateTeamInput = {
  description: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
  organizationId: Scalars['GUID']['input'];
  slug: Scalars['String']['input'];
};

export type CreateWebhookSubscriptionInput = {
  appSlug: InputMaybe<Scalars['String']['input']>;
  events: Array<Scalars['String']['input']>;
  format: InputMaybe<Scalars['String']['input']>;
  teamSlug: InputMaybe<Scalars['String']['input']>;
  url: Scalars['String']['input'];
};

export type DataImportUploadResult = {
  id?: Maybe<Scalars['ID']['output']>;
  preSignedUrl?: Maybe<Scalars['String']['output']>;
  publicUrl?: Maybe<Scalars['String']['output']>;
};

export type DecommissionClusterInputType = {
  clusterId: Scalars['GUID']['input'];
  deleteCloudInfra: Scalars['Boolean']['input'];
};

export type DeleteAlertRuleInput = {
  id: Scalars['GUID']['input'];
};

export type DeleteAppDnsRecordInput = {
  appId: Scalars['GUID']['input'];
  hostname: Scalars['String']['input'];
  recordType: Scalars['String']['input'];
};

export type DeleteAppIdentityRoleInput = {
  appId: Scalars['GUID']['input'];
};

export type DeleteAppIngressInput = {
  appId: Scalars['GUID']['input'];
  hostname: InputMaybe<Scalars['String']['input']>;
};

export type DeleteAppSecretInput = {
  appSlug: Scalars['String']['input'];
  key: Scalars['String']['input'];
};

export type DeleteRoleInput = {
  id: Scalars['GUID']['input'];
};

export type DeleteSshDeployKeyInput = {
  id: Scalars['GUID']['input'];
};

export type DeleteWebhookSubscriptionInput = {
  id: Scalars['GUID']['input'];
};

export type DeployTokenSecretReveal = {
  plaintextSecret: Scalars['String']['output'];
  token: AstroliftDeployToken;
};

export type DeployTokenSecretRevealMutationResult = {
  data?: Maybe<DeployTokenSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type DeploymentByIdInput = {
  id: Scalars['GUID']['input'];
};

export type Deploytokenrevokedpayload = {
  id: Scalars['GUID']['output'];
  revoked: Scalars['Boolean']['output'];
};

export type DeploytokenrevokedpayloadMutationResult = {
  data?: Maybe<Deploytokenrevokedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type DeprovisionManagedServiceInput = {
  deleteData: Scalars['Boolean']['input'];
  forceDestroy: Scalars['Boolean']['input'];
  id: Scalars['GUID']['input'];
};

export type DeregisterAppInput = {
  appSlug: Scalars['String']['input'];
  confirmName: Scalars['String']['input'];
};

export type DetachSecretBundleInput = {
  attachmentId: Scalars['GUID']['input'];
};

export type DisconnectSourceInput = {
  id: Scalars['GUID']['input'];
};

export type DisconnectUserSourceProviderInput = {
  confirmAccountLogin: Scalars['String']['input'];
  providerConfigId: Scalars['GUID']['input'];
};

export type EmployeeAvatarType = {
  publicPermanentUrl?: Maybe<Scalars['String']['output']>;
};

export type EmployeeEdge = {
  cursor: Scalars['String']['output'];
  node: EmployeeNode;
};

export type EmployeeNode = {
  id: Scalars['ID']['output'];
  user: EmployeeUserType;
};

export type EmployeeProfileType = {
  avatar?: Maybe<EmployeeAvatarType>;
  displayName?: Maybe<Scalars['String']['output']>;
  firstName?: Maybe<Scalars['String']['output']>;
  id: Scalars['ID']['output'];
  lastName?: Maybe<Scalars['String']['output']>;
};

export type EmployeeUserType = {
  email: Scalars['String']['output'];
  firstName: Scalars['String']['output'];
  id: Scalars['ID']['output'];
  isActive: Scalars['Boolean']['output'];
  lastName: Scalars['String']['output'];
  profile?: Maybe<EmployeeProfileType>;
};

export type EmployeesConnection = {
  edges: Array<EmployeeEdge>;
  pageInfo: EmployeesPageInfo;
  totalCount: Scalars['Int']['output'];
};

export type EmployeesPageInfo = {
  hasNextPage: Scalars['Boolean']['output'];
};

export type EntityType =
  | 'COMPONENTS'
  | 'EMPLOYEE'
  | 'FIELDFLO'
  | 'PERMISSIONS'
  | 'SITE_LABEL';

export type EnvironmentByIdInput = {
  id: Scalars['GUID']['input'];
};

export type ExportAuditEventsInput = {
  action: InputMaybe<Scalars['String']['input']>;
  actorId: InputMaybe<Scalars['String']['input']>;
  createdAtGte: InputMaybe<Scalars['DateTime']['input']>;
  createdAtLte: InputMaybe<Scalars['DateTime']['input']>;
  decision: InputMaybe<Scalars['String']['input']>;
  format: Scalars['String']['input'];
};

export type ExtendPreviewTtlInputGql = {
  days: Scalars['Int']['input'];
  id: Scalars['GUID']['input'];
};

export type FileUploadResult = {
  id?: Maybe<Scalars['ID']['output']>;
  preSignedUrl?: Maybe<Scalars['String']['output']>;
  publicUrl?: Maybe<Scalars['String']['output']>;
};

export type ForceRedeployInput = {
  appSlug: Scalars['String']['input'];
  confirmSlug: Scalars['String']['input'];
  environmentName: InputMaybe<Scalars['String']['input']>;
};

export type ForecastConfidence =
  | 'HIGH'
  | 'LOW'
  | 'MEDIUM';

export type GenerateSshDeployKeyInput = {
  appSlug: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
};

export type GoldenSignalKind =
  | 'ERRORS'
  | 'LATENCY_P50'
  | 'LATENCY_P90'
  | 'LATENCY_P99'
  | 'SATURATION_CPU'
  | 'TRAFFIC';

export type GrantRoleInput = {
  roleId: Scalars['GUID']['input'];
  scopeGuid: Scalars['GUID']['input'];
  scopeKind: Scalars['String']['input'];
  userId: Scalars['String']['input'];
};

export type GrantTeamAccessInput = {
  accessLevel: Scalars['String']['input'];
  appId: Scalars['GUID']['input'];
  teamId: Scalars['GUID']['input'];
};

export type GroupOperationInput = {
  groupId: Scalars['ID']['input'];
  operation: Scalars['String']['input'];
  userIds: Array<Scalars['ID']['input']>;
};

export type InstallClusterPrereqsInputType = {
  clusterId: Scalars['GUID']['input'];
  optionOverrides: Array<BootstrapOptionOverride>;
  selectedComponents: Array<Scalars['String']['input']>;
};

export type InstallScmWebhookInput = {
  connectionId: Scalars['GUID']['input'];
  repoFullName: Scalars['String']['input'];
  secret: InputMaybe<Scalars['String']['input']>;
  targetUrl: InputMaybe<Scalars['String']['input']>;
};

export type InstallSourceWebhookInput = {
  appSlug: Scalars['String']['input'];
};

export type LibraryMkdirResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars['Boolean']['output'];
};

export type LibraryRenameDirectoryResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars['Boolean']['output'];
};

export type LibraryRenameFileResult = {
  file?: Maybe<UploadType>;
  ok: Scalars['Boolean']['output'];
};

export type LibraryRmFileResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars['Boolean']['output'];
};

export type LibrarySetIconResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars['Boolean']['output'];
};

export type LoginResult = {
  user?: Maybe<UserType>;
};

export type LogoutAllSessionsInput = {
  keepCurrent: Scalars['Boolean']['input'];
};

export type Managedservicedeletedpayload = {
  deleted: Scalars['Boolean']['output'];
  id: Scalars['GUID']['output'];
};

export type ManagedservicedeletedpayloadMutationResult = {
  data?: Maybe<Managedservicedeletedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type Manifestpushpayload = {
  branchName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  note: Scalars['String']['output'];
  prUrl: Scalars['String']['output'];
};

export type ManifestpushpayloadMutationResult = {
  data?: Maybe<Manifestpushpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type Manifeststagepayload = {
  id: Scalars['GUID']['output'];
  rawManifest: Scalars['String']['output'];
  rawManifestStaged: Scalars['String']['output'];
  syncState: Scalars['String']['output'];
};

export type ManifeststagepayloadMutationResult = {
  data?: Maybe<Manifeststagepayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type MarkNotificationReadInput = {
  id: Scalars['GUID']['input'];
};

export type Markallreadpayload = {
  marked: Scalars['Int']['output'];
};

export type MarkallreadpayloadMutationResult = {
  data?: Maybe<Markallreadpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type MigrateAppInputGql = {
  appEnvironmentId: Scalars['GUID']['input'];
  drainSource: Scalars['Boolean']['input'];
  targetClusterId: Scalars['GUID']['input'];
};

export type MoveAppToTeamInput = {
  appId: Scalars['GUID']['input'];
  targetTeamId: Scalars['GUID']['input'];
};

export type Mutation = {
  abortDeployment: AstroliftDeploymentMutationResult;
  acceptInvitation: AstroliftInvitationMutationResult;
  acknowledgeAlertEvent: AstroliftAlertEventMutationResult;
  /** Activate or deactivate an object by its global ID. */
  activate: Scalars['Boolean']['output'];
  addAppDomain: AstroliftAppDomainMutationResult;
  addOrganizationAllowlistDomain: AstroliftOrganizationAllowlistedDomainMutationResult;
  approveDeployment: AstroliftDeploymentMutationResult;
  approveDeploymentByToken: AstroliftDeploymentMutationResult;
  archiveAppRegistryRepo: AstroliftCapabilityDeprovisionPayloadMutationResult;
  assignAstroliftAppToProject: AstroliftRegisteredAppMutationResult;
  astroliftAnonymizeUser: AstroliftAnonymizeUserPayloadMutationResult;
  astroliftConnectUserSourceProvider: AstroliftConnectUserSourceProviderPayloadMutationResult;
  astroliftDisconnectUserSourceProvider: AstroliftDisconnectUserSourceProviderPayloadMutationResult;
  attachSecretBundle: AstroliftAppSecretBundleAttachmentMutationResult;
  bringClusterIntoManagement: AstroliftTenantClusterMutationResult;
  bulkApproveDeployments: AstroliftBulkDeploymentResultDataMutationResult;
  bulkImportAppSecrets: BulkimportpayloadMutationResult;
  bulkRejectDeployments: AstroliftBulkDeploymentResultDataMutationResult;
  configureProviderPlugin: ProviderpluginconfigpayloadMutationResult;
  /** Confirm or update a previously uploaded file. Set delete=true to soft-delete the upload. */
  confirmPreSignedUrlImageUpload: ConfirmUploadResult;
  connectSource: AstroliftSourceConnectionMutationResult;
  createAlertRule: AstroliftAlertRuleMutationResult;
  createApiToken: AstroliftApiTokenPlaintextMutationResult;
  createDeployToken: DeployTokenSecretRevealMutationResult;
  createIdentityProvider: AstroliftIdentityProviderMutationResult;
  createInvitation: AstroliftInvitationCreatedMutationResult;
  createManagedDomain: AstroliftManagedDomainMutationResult;
  createOrganization: AstroliftOrganizationMutationResult;
  createPolicy: AstroliftPolicyMutationResult;
  createProject: AstroliftProjectMutationResult;
  createRole: AstroliftRoleMutationResult;
  createTeam: AstroliftTeamMutationResult;
  createWebhookSubscription: WebhookSecretRevealMutationResult;
  /** Create a new workflow definition (staff only). */
  createWorkflowDefinition: MutationResult;
  decommissionCluster: AstroliftTenantClusterMutationResult;
  /** Delete an object by its global ID (soft-delete via delete_check). */
  delete: Scalars['Boolean']['output'];
  deleteAlertRule: AlertruledeletedpayloadMutationResult;
  deleteAppDnsRecord: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppIdentityRole: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppIngress: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppSecret: AppsecretwritepayloadMutationResult;
  deleteSshDeployKey: AstroliftSshDeployKeyMutationResult;
  deleteWebhookSubscription: SoftdeletepayloadMutationResult;
  /** Delete a workflow definition by slug (staff only). */
  deleteWorkflowDefinition: MutationResult;
  deprovisionManagedService: ManagedservicedeletedpayloadMutationResult;
  deregisterAstroliftApp: AstroliftDeregisterAppPayloadMutationResult;
  detachSecretBundle: AttachmentremovedpayloadMutationResult;
  disconnectSource: AstroliftSourceConnectionMutationResult;
  exportAuditEvents: AstroliftAuditExportMutationResult;
  extendPreviewTtl: AstroliftPreviewEnvironmentMutationResult;
  /** Upload a file and get a pre-signed URL. Creates a FileUpload wrapper around the Upload. */
  fileUpload: FileUploadResult;
  forceAstroliftRedeploy: AstroliftForceRedeployPayloadMutationResult;
  /** Generate a temporary authentication token for Rocket.Chat. TTL is configured on the Rocket.Chat server. */
  generateRocketChatToken: Scalars['String']['output'];
  generateSshDeployKey: AstroliftSshDeployKeyCreatedMutationResult;
  grantRole: AstroliftRoleBindingMutationResult;
  grantTeamAccessToApp: AstroliftAppTeamAccessMutationResult;
  installAstroliftSourceWebhook: AstroliftInstallSourceWebhookPayloadMutationResult;
  installClusterPrereqs: AstroliftTenantClusterMutationResult;
  installScmWebhook: AstroliftScmWebhookInstallationMutationResult;
  /** Create a new directory in the library. */
  libraryMkdir: LibraryMkdirResult;
  /** Rename a directory in the library. */
  libraryRenameDir: LibraryRenameDirectoryResult;
  /** Rename a file in the library. */
  libraryRenameFile: LibraryRenameFileResult;
  /** Remove a file from a library directory. */
  libraryRmFile: LibraryRmFileResult;
  /** Remove a directory from the library. */
  libraryRmdir: Scalars['Boolean']['output'];
  /** Set the icon for a library directory. */
  librarySetIcon: LibrarySetIconResult;
  /** Authenticate a user with username and password. */
  login: LoginResult;
  /** Logout the current user. */
  logout: Scalars['Boolean']['output'];
  logoutAllSessions: AstroliftLogoutAllSessionsPayloadMutationResult;
  markAllNotificationsRead: MarkallreadpayloadMutationResult;
  markNotificationRead: AstroliftNotificationMutationResult;
  /** Create or update a Metabase chart via MetabaseChartSerializer. */
  metabaseChart: MutationResult;
  migrateAppToCluster: AstroliftAppEnvironmentMutationResult;
  moveAppToTeam: AstroliftRegisteredAppMutationResult;
  /** Create or update a notification via NotificationSerializer. */
  notification: MutationResult;
  /** Mark a notification as read. */
  notificationRead: Scalars['Boolean']['output'];
  organization: OrganizationMutationResult;
  organizationMemberStatus: MutationResult;
  /** Force a workflow instance to a specific state (admin override). */
  overrideWorkflowState: MutationResult;
  pauseAppIngress: AstroliftAppEnvironmentMutationResult;
  pauseEnvironment: AstroliftAppEnvironmentMutationResult;
  /** Add or remove users from a permission group. */
  permissionGroupOperation: MutationResult;
  /** Authenticate a PIN transaction. The proxy_user parameter allows acting on behalf of another user. */
  pinTransaction: Scalars['Boolean']['output'];
  /** Update or set the user's PIN. */
  pinUpdate: Scalars['Boolean']['output'];
  /** Get a pre-signed URL for uploading an image or file. Optionally attach it to an entity via owner_container_property. */
  preSignedUrlImageUpload: PreSignedUrlUploadResult;
  /** Process a previously uploaded data import file. */
  processFile: ProcessFileResult;
  /** Update user profile via ProfileSerializer (restricted). */
  profile: MutationResult;
  /** Upload an image for a specific profile image field (avatar, signature). Supports the approval request workflow for non-whitelisted fields. */
  profileImageFieldUpload: ProfileImageFieldUploadResult;
  /** Request deletion of a user account. Requires PROFILE_DELETE_USERS permission to delete other users. */
  profileRequestDeleteUser: Scalars['Boolean']['output'];
  /** Request a password reset email. Requires PROFILE_CHANGE_RESET_PASSWORD_USERS permission to send to other users. */
  profileRequestPwdChange: Scalars['Boolean']['output'];
  provisionManagedService: AstroliftManagedServiceMutationResult;
  pushAstroliftCiSecretsToRepo: AstroliftPushCiSecretsPayloadMutationResult;
  pushAstroliftCiWorkflowToRepo: AstroliftPushCiWorkflowPayloadMutationResult;
  pushCiWorkflow: AstroliftScmPushCiWorkflowResultMutationResult;
  pushManifestToRepo: ManifestpushpayloadMutationResult;
  recheckDomainValidation: AstroliftAppDomainMutationResult;
  recordClusterBootstrapRun: BootstraprunrecordedpayloadMutationResult;
  redeployApp: AstroliftDeploymentMutationResult;
  refreshClusterManagement: AstroliftTenantClusterMutationResult;
  registerApp: AstroliftRegisteredAppMutationResult;
  registerTenantCluster: AstroliftTenantClusterMutationResult;
  rejectDeployment: AstroliftDeploymentMutationResult;
  rejectDeploymentByToken: AstroliftDeploymentMutationResult;
  removeAppDomain: AppdomainremovedpayloadMutationResult;
  removeOrganizationAllowlistDomain: SoftdeletepayloadMutationResult;
  restartAstroliftWorkload: AstroliftWorkloadOpPayloadMutationResult;
  resumeAppIngress: AstroliftAppEnvironmentMutationResult;
  resumeEnvironment: AstroliftAppEnvironmentMutationResult;
  resyncAstroliftManifestFromRepo: ResyncManifestPayloadMutationResult;
  revealAppSecret: AstroliftRevealedSecretMutationResult;
  revokeApiToken: SoftdeletepayloadMutationResult;
  revokeAppCertificate: AstroliftCapabilityDeprovisionPayloadMutationResult;
  revokeDeployToken: DeploytokenrevokedpayloadMutationResult;
  revokeInvitation: AstroliftInvitationMutationResult;
  revokeRoleBinding: SoftdeletepayloadMutationResult;
  revokeTeamAccessFromApp: SoftdeletepayloadMutationResult;
  rollbackDeployment: AstroliftDeploymentMutationResult;
  rotateDeployToken: DeployTokenSecretRevealMutationResult;
  rotateOutboundWebhookSecret: WebhookSecretRevealMutationResult;
  rotateSecretBundle: AstroliftSecretBundleMutationResult;
  rotateWebhookSecret: AstroliftScmWebhookSecretRevealMutationResult;
  runAstroliftJobOnce: AstroliftRunJobOncePayloadMutationResult;
  scaleAstroliftWorkload: AstroliftWorkloadOpPayloadMutationResult;
  setActiveIdentityProvider: AstroliftIdentityProviderMutationResult;
  setAppSecret: AppsecretwritepayloadMutationResult;
  setAppSubdomain: AstroliftRegisteredAppMutationResult;
  /** Cancel a sign request. Requires SIGNREQUEST_CHANGE_CANCEL permission. */
  signRequestCancel: Scalars['Boolean']['output'];
  /** Sign a sign request. Requires SIGNREQUEST_CHANGE_SIGN permission and an active PIN transaction. Status must be SIGN_REQUIRED. */
  signRequestSign: Scalars['Boolean']['output'];
  /** Request a sign from a user. The user must have SIGNREQUEST_CHANGE_SIGN permission. */
  signRequestUser: Scalars['Boolean']['output'];
  softDeleteApp: SoftdeletepayloadMutationResult;
  softDeleteIdentityProvider: SoftdeletepayloadMutationResult;
  softDeleteManagedDomain: SoftdeletepayloadMutationResult;
  softDeleteOrganization: SoftdeletepayloadMutationResult;
  softDeletePolicy: SoftdeletepayloadMutationResult;
  softDeleteProject: SoftdeletepayloadMutationResult;
  softDeleteRole: SoftdeletepayloadMutationResult;
  softDeleteTeam: SoftdeletepayloadMutationResult;
  startDeployment: AstroliftDeploymentMutationResult;
  /** Start a workflow for an object. */
  startWorkflow: StartWorkflowResult;
  /** Switch the active user (impersonation). */
  switchUser: SwitchUserResult;
  syncManifestFromRepo: ManifeststagepayloadMutationResult;
  tearDownApp: SoftdeletepayloadMutationResult;
  tearDownPreview: AstroliftDeploymentMutationResult;
  testNotificationChannel: AstroliftNotificationMutationResult;
  testWebhookSubscription: AstroliftWebhookTestResultMutationResult;
  transferApp: AstroliftRegisteredAppMutationResult;
  /** Transition a workflow instance to a new state. */
  transitionWorkflow: MutationResult;
  triggerAstroliftDeployWorkflow: AstroliftTriggerDeployWorkflowPayloadMutationResult;
  unregisterTenantCluster: SoftdeletepayloadMutationResult;
  updateAlertRule: AstroliftAlertRuleMutationResult;
  updateApp: AstroliftRegisteredAppMutationResult;
  updateAstroliftSecurityPolicy: AstroliftRegisteredAppMutationResult;
  updateIdentityProvider: AstroliftIdentityProviderMutationResult;
  updateManagedDomain: AstroliftManagedDomainMutationResult;
  updateManagedService: AstroliftManagedServiceMutationResult;
  updateManifest: ManifeststagepayloadMutationResult;
  updateMyProfile: AstroliftMyProfileMutationResult;
  updateOrganization: AstroliftOrganizationMutationResult;
  updatePolicy: AstroliftPolicyMutationResult;
  updateProject: AstroliftProjectMutationResult;
  updateRole: AstroliftRoleMutationResult;
  updateSourceConnection: AstroliftSourceConnectionMutationResult;
  updateTeam: AstroliftTeamMutationResult;
  updateTenantCluster: AstroliftTenantClusterMutationResult;
  updateWebhookSubscription: AstroliftWebhookSubscriptionMutationResult;
  /** Update an existing workflow definition (staff only). */
  updateWorkflowDefinition: MutationResult;
  uploadCustomDomainCertificate: AstroliftAppDomainMutationResult;
  /** Upload a data import file and get a pre-signed URL. */
  uploadTextFile: DataImportUploadResult;
  upsertOrganization: OrganizationMutationResult;
  /** Upsert user profile via UtilityForm.apply_forms. */
  upsertUser: UpsertUserResult;
};


export type MutationAbortDeploymentArgs = {
  input: AbortDeploymentInput;
};


export type MutationAcceptInvitationArgs = {
  input: AcceptInvitationInput;
};


export type MutationAcknowledgeAlertEventArgs = {
  input: AcknowledgeAlertEventInput;
};


export type MutationActivateArgs = {
  active?: Scalars['Boolean']['input'];
  gid: Scalars['ID']['input'];
};


export type MutationAddAppDomainArgs = {
  input: AddAppDomainInput;
};


export type MutationAddOrganizationAllowlistDomainArgs = {
  input: AddOrganizationAllowlistDomainInput;
};


export type MutationApproveDeploymentArgs = {
  input: DeploymentByIdInput;
};


export type MutationApproveDeploymentByTokenArgs = {
  input: ApproveByTokenInput;
};


export type MutationArchiveAppRegistryRepoArgs = {
  input: ArchiveAppRegistryRepoInput;
};


export type MutationAssignAstroliftAppToProjectArgs = {
  input: AssignAppToProjectInput;
};


export type MutationAstroliftAnonymizeUserArgs = {
  input: AstroliftAnonymizeUserInput;
};


export type MutationAstroliftConnectUserSourceProviderArgs = {
  input: ConnectUserSourceProviderInput;
};


export type MutationAstroliftDisconnectUserSourceProviderArgs = {
  input: DisconnectUserSourceProviderInput;
};


export type MutationAttachSecretBundleArgs = {
  input: AttachSecretBundleInput;
};


export type MutationBringClusterIntoManagementArgs = {
  input: BringClusterIntoManagementInputType;
};


export type MutationBulkApproveDeploymentsArgs = {
  input: BulkApproveDeploymentsInput;
};


export type MutationBulkImportAppSecretsArgs = {
  input: BulkImportAppSecretsInput;
};


export type MutationBulkRejectDeploymentsArgs = {
  input: BulkRejectDeploymentsInput;
};


export type MutationConfigureProviderPluginArgs = {
  input: ConfigureProviderPluginInput;
};


export type MutationConfirmPreSignedUrlImageUploadArgs = {
  delete?: InputMaybe<Scalars['Boolean']['input']>;
  expirationDate?: InputMaybe<Scalars['Date']['input']>;
  metadata?: InputMaybe<Scalars['JSON']['input']>;
  publicUrl?: InputMaybe<Scalars['String']['input']>;
  uploadId?: InputMaybe<Scalars['ID']['input']>;
};


export type MutationConnectSourceArgs = {
  input: ConnectSourceInput;
};


export type MutationCreateAlertRuleArgs = {
  input: CreateAlertRuleInput;
};


export type MutationCreateApiTokenArgs = {
  input: CreateApiTokenInput;
};


export type MutationCreateDeployTokenArgs = {
  input: CreateDeployTokenInput;
};


export type MutationCreateIdentityProviderArgs = {
  input: CreateIdentityProviderInput;
};


export type MutationCreateInvitationArgs = {
  input: CreateInvitationInput;
};


export type MutationCreateManagedDomainArgs = {
  input: CreateManagedDomainInput;
};


export type MutationCreateOrganizationArgs = {
  input: CreateOrganizationInput;
};


export type MutationCreatePolicyArgs = {
  input: CreatePolicyInput;
};


export type MutationCreateProjectArgs = {
  input: CreateProjectInput;
};


export type MutationCreateRoleArgs = {
  input: CreateRoleInput;
};


export type MutationCreateTeamArgs = {
  input: CreateTeamInput;
};


export type MutationCreateWebhookSubscriptionArgs = {
  input: CreateWebhookSubscriptionInput;
};


export type MutationCreateWorkflowDefinitionArgs = {
  description?: InputMaybe<Scalars['String']['input']>;
  isEnabled?: Scalars['Boolean']['input'];
  modelLabel: Scalars['String']['input'];
  name: Scalars['String']['input'];
  slug: Scalars['String']['input'];
  states: Scalars['JSON']['input'];
  transitions: Scalars['JSON']['input'];
};


export type MutationDecommissionClusterArgs = {
  input: DecommissionClusterInputType;
};


export type MutationDeleteArgs = {
  gid: Scalars['ID']['input'];
};


export type MutationDeleteAlertRuleArgs = {
  input: DeleteAlertRuleInput;
};


export type MutationDeleteAppDnsRecordArgs = {
  input: DeleteAppDnsRecordInput;
};


export type MutationDeleteAppIdentityRoleArgs = {
  input: DeleteAppIdentityRoleInput;
};


export type MutationDeleteAppIngressArgs = {
  input: DeleteAppIngressInput;
};


export type MutationDeleteAppSecretArgs = {
  input: DeleteAppSecretInput;
};


export type MutationDeleteSshDeployKeyArgs = {
  input: DeleteSshDeployKeyInput;
};


export type MutationDeleteWebhookSubscriptionArgs = {
  input: DeleteWebhookSubscriptionInput;
};


export type MutationDeleteWorkflowDefinitionArgs = {
  slug: Scalars['String']['input'];
};


export type MutationDeprovisionManagedServiceArgs = {
  input: DeprovisionManagedServiceInput;
};


export type MutationDeregisterAstroliftAppArgs = {
  input: DeregisterAppInput;
};


export type MutationDetachSecretBundleArgs = {
  input: DetachSecretBundleInput;
};


export type MutationDisconnectSourceArgs = {
  input: DisconnectSourceInput;
};


export type MutationExportAuditEventsArgs = {
  input: ExportAuditEventsInput;
};


export type MutationExtendPreviewTtlArgs = {
  input: ExtendPreviewTtlInputGql;
};


export type MutationFileUploadArgs = {
  description?: InputMaybe<Scalars['String']['input']>;
  fileUploadGid?: InputMaybe<Scalars['ID']['input']>;
  isPublic?: InputMaybe<Scalars['Boolean']['input']>;
  metadata?: InputMaybe<Scalars['JSON']['input']>;
  mimetype: Scalars['String']['input'];
  name?: InputMaybe<Scalars['String']['input']>;
};


export type MutationForceAstroliftRedeployArgs = {
  input: ForceRedeployInput;
};


export type MutationGenerateSshDeployKeyArgs = {
  input: GenerateSshDeployKeyInput;
};


export type MutationGrantRoleArgs = {
  input: GrantRoleInput;
};


export type MutationGrantTeamAccessToAppArgs = {
  input: GrantTeamAccessInput;
};


export type MutationInstallAstroliftSourceWebhookArgs = {
  input: InstallSourceWebhookInput;
};


export type MutationInstallClusterPrereqsArgs = {
  input: InstallClusterPrereqsInputType;
};


export type MutationInstallScmWebhookArgs = {
  input: InstallScmWebhookInput;
};


export type MutationLibraryMkdirArgs = {
  icon?: InputMaybe<Scalars['ID']['input']>;
  name: Scalars['String']['input'];
  parentGuid?: InputMaybe<Scalars['ID']['input']>;
};


export type MutationLibraryRenameDirArgs = {
  guid: Scalars['ID']['input'];
  name: Scalars['String']['input'];
};


export type MutationLibraryRenameFileArgs = {
  guid: Scalars['ID']['input'];
  name: Scalars['String']['input'];
};


export type MutationLibraryRmFileArgs = {
  directory: Scalars['ID']['input'];
  file: Scalars['ID']['input'];
};


export type MutationLibraryRmdirArgs = {
  directoryGuid: Scalars['ID']['input'];
};


export type MutationLibrarySetIconArgs = {
  directory: Scalars['ID']['input'];
  file: Scalars['ID']['input'];
};


export type MutationLoginArgs = {
  password: Scalars['String']['input'];
  username: Scalars['String']['input'];
};


export type MutationLogoutAllSessionsArgs = {
  input: LogoutAllSessionsInput;
};


export type MutationMarkNotificationReadArgs = {
  input: MarkNotificationReadInput;
};


export type MutationMetabaseChartArgs = {
  input: Scalars['JSON']['input'];
};


export type MutationMigrateAppToClusterArgs = {
  input: MigrateAppInputGql;
};


export type MutationMoveAppToTeamArgs = {
  input: MoveAppToTeamInput;
};


export type MutationNotificationArgs = {
  input: Scalars['JSON']['input'];
};


export type MutationNotificationReadArgs = {
  gid: Scalars['ID']['input'];
};


export type MutationOrganizationArgs = {
  input: OrganizationInput;
};


export type MutationOrganizationMemberStatusArgs = {
  input: OrganizationMemberStatusInput;
};


export type MutationOverrideWorkflowStateArgs = {
  instanceId: Scalars['ID']['input'];
  note?: Scalars['String']['input'];
  toState: Scalars['String']['input'];
};


export type MutationPauseAppIngressArgs = {
  input: EnvironmentByIdInput;
};


export type MutationPauseEnvironmentArgs = {
  input: EnvironmentByIdInput;
};


export type MutationPermissionGroupOperationArgs = {
  input: GroupOperationInput;
};


export type MutationPinTransactionArgs = {
  pin: Scalars['String']['input'];
  proxyUser?: InputMaybe<Scalars['ID']['input']>;
};


export type MutationPinUpdateArgs = {
  pin: Scalars['String']['input'];
};


export type MutationPreSignedUrlImageUploadArgs = {
  description?: InputMaybe<Scalars['String']['input']>;
  globalId: Scalars['ID']['input'];
  location?: InputMaybe<UploadLocation>;
  metadata?: InputMaybe<Scalars['JSON']['input']>;
  mimetype: Scalars['String']['input'];
  name?: InputMaybe<Scalars['String']['input']>;
  ownerContainerProperty?: InputMaybe<Scalars['String']['input']>;
  uuid?: InputMaybe<Scalars['UUID']['input']>;
};


export type MutationProcessFileArgs = {
  entityType: EntityType;
  processId?: InputMaybe<Scalars['ID']['input']>;
  uploadedFileId: Scalars['ID']['input'];
};


export type MutationProfileArgs = {
  input: Scalars['JSON']['input'];
};


export type MutationProfileImageFieldUploadArgs = {
  field?: InputMaybe<ProfileImageField>;
  globalId: Scalars['ID']['input'];
  metadata?: InputMaybe<Scalars['JSON']['input']>;
  mimetype: Scalars['String']['input'];
};


export type MutationProfileRequestDeleteUserArgs = {
  userGid?: InputMaybe<Scalars['ID']['input']>;
};


export type MutationProfileRequestPwdChangeArgs = {
  userGid?: InputMaybe<Scalars['ID']['input']>;
};


export type MutationProvisionManagedServiceArgs = {
  input: ProvisionManagedServiceInput;
};


export type MutationPushAstroliftCiSecretsToRepoArgs = {
  input: PushCiSecretsToRepoInput;
};


export type MutationPushAstroliftCiWorkflowToRepoArgs = {
  input: PushCiWorkflowToRepoInput;
};


export type MutationPushCiWorkflowArgs = {
  input: PushCiWorkflowInput;
};


export type MutationPushManifestToRepoArgs = {
  input: PushManifestToRepoInput;
};


export type MutationRecheckDomainValidationArgs = {
  input: RecheckDomainValidationInput;
};


export type MutationRecordClusterBootstrapRunArgs = {
  input: RecordClusterBootstrapRunInput;
};


export type MutationRedeployAppArgs = {
  input: DeploymentByIdInput;
};


export type MutationRefreshClusterManagementArgs = {
  input: RefreshClusterManagementInputType;
};


export type MutationRegisterAppArgs = {
  input: RegisterAppInput;
};


export type MutationRegisterTenantClusterArgs = {
  input: RegisterTenantClusterInput;
};


export type MutationRejectDeploymentArgs = {
  input: AbortDeploymentInput;
};


export type MutationRejectDeploymentByTokenArgs = {
  input: RejectByTokenInput;
};


export type MutationRemoveAppDomainArgs = {
  input: RemoveAppDomainInput;
};


export type MutationRemoveOrganizationAllowlistDomainArgs = {
  input: RemoveOrganizationAllowlistDomainInput;
};


export type MutationRestartAstroliftWorkloadArgs = {
  input: RestartWorkloadInput;
};


export type MutationResumeAppIngressArgs = {
  input: EnvironmentByIdInput;
};


export type MutationResumeEnvironmentArgs = {
  input: EnvironmentByIdInput;
};


export type MutationResyncAstroliftManifestFromRepoArgs = {
  input: ResyncManifestFromRepoInput;
};


export type MutationRevealAppSecretArgs = {
  input: RevealAppSecretInput;
};


export type MutationRevokeApiTokenArgs = {
  input: RevokeApiTokenInput;
};


export type MutationRevokeAppCertificateArgs = {
  input: RevokeAppCertificateInput;
};


export type MutationRevokeDeployTokenArgs = {
  input: RevokeDeployTokenInput;
};


export type MutationRevokeInvitationArgs = {
  input: RevokeInvitationInput;
};


export type MutationRevokeRoleBindingArgs = {
  input: RevokeRoleBindingInput;
};


export type MutationRevokeTeamAccessFromAppArgs = {
  input: RevokeTeamAccessInput;
};


export type MutationRollbackDeploymentArgs = {
  input: DeploymentByIdInput;
};


export type MutationRotateDeployTokenArgs = {
  input: RotateDeployTokenInput;
};


export type MutationRotateOutboundWebhookSecretArgs = {
  input: RotateOutboundWebhookSecretInput;
};


export type MutationRotateSecretBundleArgs = {
  input: RotateSecretBundleInput;
};


export type MutationRotateWebhookSecretArgs = {
  input: RotateWebhookSecretInput;
};


export type MutationRunAstroliftJobOnceArgs = {
  input: RunJobOnceInput;
};


export type MutationScaleAstroliftWorkloadArgs = {
  input: ScaleWorkloadInput;
};


export type MutationSetActiveIdentityProviderArgs = {
  input: SetActiveIdentityProviderInput;
};


export type MutationSetAppSecretArgs = {
  input: SetAppSecretInput;
};


export type MutationSetAppSubdomainArgs = {
  input: SetAppSubdomainInput;
};


export type MutationSignRequestCancelArgs = {
  gid: Scalars['String']['input'];
  note: Scalars['String']['input'];
};


export type MutationSignRequestSignArgs = {
  gid: Scalars['String']['input'];
};


export type MutationSignRequestUserArgs = {
  gid: Scalars['String']['input'];
  userToRequest: Scalars['ID']['input'];
};


export type MutationSoftDeleteAppArgs = {
  input: SoftDeleteAppInput;
};


export type MutationSoftDeleteIdentityProviderArgs = {
  input: SoftDeleteByGuidInput;
};


export type MutationSoftDeleteManagedDomainArgs = {
  input: SoftDeleteManagedDomainInput;
};


export type MutationSoftDeleteOrganizationArgs = {
  input: SoftDeleteByGuidInput;
};


export type MutationSoftDeletePolicyArgs = {
  input: SoftDeleteByGuidInput;
};


export type MutationSoftDeleteProjectArgs = {
  input: SoftDeleteByGuidInput;
};


export type MutationSoftDeleteRoleArgs = {
  input: DeleteRoleInput;
};


export type MutationSoftDeleteTeamArgs = {
  input: SoftDeleteByGuidInput;
};


export type MutationStartDeploymentArgs = {
  input: StartDeploymentInput;
};


export type MutationStartWorkflowArgs = {
  modelLabel: Scalars['String']['input'];
  objectId: Scalars['Int']['input'];
  workflowSlug: Scalars['String']['input'];
};


export type MutationSwitchUserArgs = {
  id: Scalars['ID']['input'];
};


export type MutationSyncManifestFromRepoArgs = {
  input: SyncManifestFromRepoInput;
};


export type MutationTearDownAppArgs = {
  input: TearDownAppInput;
};


export type MutationTearDownPreviewArgs = {
  input: TearDownPreviewInputGql;
};


export type MutationTestNotificationChannelArgs = {
  input: TestNotificationInput;
};


export type MutationTestWebhookSubscriptionArgs = {
  input: TestWebhookInput;
};


export type MutationTransferAppArgs = {
  input: TransferAppInput;
};


export type MutationTransitionWorkflowArgs = {
  instanceId: Scalars['ID']['input'];
  note?: Scalars['String']['input'];
  toState: Scalars['String']['input'];
};


export type MutationTriggerAstroliftDeployWorkflowArgs = {
  input: TriggerDeployWorkflowInput;
};


export type MutationUnregisterTenantClusterArgs = {
  input: UnregisterTenantClusterInput;
};


export type MutationUpdateAlertRuleArgs = {
  input: UpdateAlertRuleInput;
};


export type MutationUpdateAppArgs = {
  input: UpdateAppInput;
};


export type MutationUpdateAstroliftSecurityPolicyArgs = {
  input: UpdateSecurityPolicyInput;
};


export type MutationUpdateIdentityProviderArgs = {
  input: UpdateIdentityProviderInput;
};


export type MutationUpdateManagedDomainArgs = {
  input: UpdateManagedDomainInput;
};


export type MutationUpdateManagedServiceArgs = {
  input: UpdateManagedServiceInput;
};


export type MutationUpdateManifestArgs = {
  input: UpdateManifestInput;
};


export type MutationUpdateMyProfileArgs = {
  input: UpdateMyProfileInput;
};


export type MutationUpdateOrganizationArgs = {
  input: UpdateOrganizationInput;
};


export type MutationUpdatePolicyArgs = {
  input: UpdatePolicyInput;
};


export type MutationUpdateProjectArgs = {
  input: UpdateProjectInput;
};


export type MutationUpdateRoleArgs = {
  input: UpdateRoleInput;
};


export type MutationUpdateSourceConnectionArgs = {
  input: UpdateSourceConnectionInput;
};


export type MutationUpdateTeamArgs = {
  input: UpdateTeamInput;
};


export type MutationUpdateTenantClusterArgs = {
  input: UpdateTenantClusterInput;
};


export type MutationUpdateWebhookSubscriptionArgs = {
  input: UpdateWebhookSubscriptionInput;
};


export type MutationUpdateWorkflowDefinitionArgs = {
  description?: InputMaybe<Scalars['String']['input']>;
  isEnabled?: InputMaybe<Scalars['Boolean']['input']>;
  modelLabel?: InputMaybe<Scalars['String']['input']>;
  name?: InputMaybe<Scalars['String']['input']>;
  slug: Scalars['String']['input'];
  states?: InputMaybe<Scalars['JSON']['input']>;
  transitions?: InputMaybe<Scalars['JSON']['input']>;
};


export type MutationUploadCustomDomainCertificateArgs = {
  input: UploadCustomDomainCertificateInput;
};


export type MutationUploadTextFileArgs = {
  metadata?: InputMaybe<Scalars['JSON']['input']>;
  mimetype: Scalars['String']['input'];
};


export type MutationUpsertOrganizationArgs = {
  input: UpsertOrganizationInput;
};


export type MutationUpsertUserArgs = {
  input: UserInput;
};

export type MutationError = {
  code: Scalars['String']['output'];
  field?: Maybe<Scalars['String']['output']>;
  message: Scalars['String']['output'];
};

/** Standard mutation result with ok flag and validation errors. */
export type MutationResult = {
  errors: Array<ValidationError>;
  ok: Scalars['Boolean']['output'];
};

export type OrganizationInput = {
  id: InputMaybe<Scalars['ID']['input']>;
  website: InputMaybe<Scalars['String']['input']>;
};

export type OrganizationMemberStatusInput = {
  isActive: Scalars['Boolean']['input'];
  organizationId: InputMaybe<Scalars['ID']['input']>;
  userId: Scalars['ID']['input'];
};

export type OrganizationMemberType = {
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  isActive: Scalars['Boolean']['output'];
  member?: Maybe<Scalars['JSON']['output']>;
  organization?: Maybe<OrganizationType>;
  updatedAt: Scalars['DateTime']['output'];
  version: Scalars['Int']['output'];
};

export type OrganizationMutationResult = {
  errors: Array<ValidationError>;
  id?: Maybe<Scalars['ID']['output']>;
  ok: Scalars['Boolean']['output'];
};

export type OrganizationType = {
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  description?: Maybe<Scalars['String']['output']>;
  guid: Scalars['UUID']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
  version: Scalars['Int']['output'];
  website?: Maybe<Scalars['String']['output']>;
};

export type PermissionComparison = {
  differences: Array<PermissionDiff>;
  onlyA: Array<Scalars['String']['output']>;
  onlyB: Array<Scalars['String']['output']>;
  shared: Array<Scalars['String']['output']>;
  userAUsername: Scalars['String']['output'];
  userBUsername: Scalars['String']['output'];
};

export type PermissionDiagnosis = {
  granted: Scalars['Boolean']['output'];
  isSuperuser: Scalars['Boolean']['output'];
  permission: Scalars['String']['output'];
  steps: Array<PermissionTraceStep>;
  userId: Scalars['ID']['output'];
  username: Scalars['String']['output'];
};

export type PermissionDiff = {
  codename: Scalars['String']['output'];
  name: Scalars['String']['output'];
  userAHas: Scalars['Boolean']['output'];
  userBHas: Scalars['Boolean']['output'];
};

export type PermissionEntry = {
  appLabel: Scalars['String']['output'];
  codename: Scalars['String']['output'];
  grantedViaGroups: Array<Scalars['String']['output']>;
  model: Scalars['String']['output'];
  name: Scalars['String']['output'];
};

export type PermissionTraceStep = {
  check: Scalars['String']['output'];
  detail: Scalars['String']['output'];
  result: Scalars['Boolean']['output'];
};

export type PreSignedUrlUploadResult = {
  fileUrl?: Maybe<Scalars['String']['output']>;
  preSignedUrl?: Maybe<Scalars['String']['output']>;
  publicUrl?: Maybe<Scalars['String']['output']>;
  uuid?: Maybe<Scalars['UUID']['output']>;
};

export type ProcessFileResult = {
  errors?: Maybe<Scalars['Int']['output']>;
  processId?: Maybe<Scalars['ID']['output']>;
  status?: Maybe<Scalars['String']['output']>;
  successful?: Maybe<Scalars['Int']['output']>;
};

export type ProfileImageField =
  | 'AVATAR'
  | 'SIGNATURE';

export type ProfileImageFieldUploadResult = {
  upload?: Maybe<UploadType>;
};

export type ProfileInput = {
  avatar: InputMaybe<Scalars['String']['input']>;
};

export type ProfileType = {
  avatar?: Maybe<UploadType>;
  birthDate?: Maybe<Scalars['DateTime']['output']>;
  displayName?: Maybe<Scalars['String']['output']>;
  /** Direct reference to User.email. */
  email?: Maybe<Scalars['String']['output']>;
  firstName?: Maybe<Scalars['String']['output']>;
  guid: Scalars['UUID']['output'];
  hasPin: Scalars['Boolean']['output'];
  isActive: Scalars['Boolean']['output'];
  lastName?: Maybe<Scalars['String']['output']>;
  nickname?: Maybe<Scalars['String']['output']>;
  preferredLanguage?: Maybe<Scalars['String']['output']>;
  signature?: Maybe<UploadType>;
  timezone?: Maybe<Scalars['String']['output']>;
  username?: Maybe<Scalars['String']['output']>;
};

export type Providerpluginconfigpayload = {
  organizationScoped: Scalars['Boolean']['output'];
  pluginSlug: Scalars['String']['output'];
};

export type ProviderpluginconfigpayloadMutationResult = {
  data?: Maybe<Providerpluginconfigpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type ProvisionManagedServiceInput = {
  appSlug: Scalars['String']['input'];
  config: InputMaybe<Scalars['JSON']['input']>;
  environmentName: Scalars['String']['input'];
  kind: Scalars['String']['input'];
  name: InputMaybe<Scalars['String']['input']>;
  variant: InputMaybe<Scalars['String']['input']>;
};

export type PushCiSecretsToRepoInput = {
  appSlug: Scalars['String']['input'];
};

export type PushCiWorkflowInput = {
  appId: Scalars['GUID']['input'];
  branch: InputMaybe<Scalars['String']['input']>;
  commitMessage: InputMaybe<Scalars['String']['input']>;
  connectionId: Scalars['GUID']['input'];
  filePath: InputMaybe<Scalars['String']['input']>;
};

export type PushCiWorkflowToRepoInput = {
  appSlug: Scalars['String']['input'];
};

export type PushManifestToRepoInput = {
  branchName: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  prBody: InputMaybe<Scalars['String']['input']>;
  prTitle: InputMaybe<Scalars['String']['input']>;
};

export type Query = {
  assignableAstroliftProjects: Array<AstroliftProject>;
  astroliftActiveIdentityProvider?: Maybe<AstroliftIdentityProvider>;
  astroliftActiveSessions: Array<AstroliftActiveSession>;
  astroliftAlertEvents: Array<AstroliftAlertEvent>;
  astroliftAlertRules: Array<AstroliftAlertRule>;
  astroliftApiTokens: Array<AstroliftApiToken>;
  astroliftApp?: Maybe<AstroliftRegisteredApp>;
  astroliftAppCertificates: Array<AstroliftAppCertificate>;
  astroliftAppCountForCluster: Scalars['Int']['output'];
  astroliftAppDeployTokens: Array<AstroliftDeployToken>;
  astroliftAppDnsRecords: Array<AstroliftAppDnsRecord>;
  astroliftAppDomains: Array<AstroliftAppDomain>;
  astroliftAppGoldenSignals: Array<AstroliftAppGoldenSignal>;
  astroliftAppHealthSummary: Array<AstroliftAppHealthSummary>;
  astroliftAppIdentityBinding?: Maybe<AstroliftAppIdentityBinding>;
  astroliftAppMetrics?: Maybe<AstroliftAppMetrics>;
  astroliftAppPods: Array<AstroliftAppPod>;
  astroliftAppSecretBundleAttachments: Array<AstroliftAppSecretBundleAttachment>;
  astroliftAppSecrets: Array<AstroliftAppSecret>;
  astroliftAppStatusCodeBreakdown?: Maybe<AstroliftStatusCodeBreakdown>;
  astroliftAppTeamAccesses: Array<AstroliftAppTeamAccess>;
  astroliftApps: Array<AstroliftRegisteredApp>;
  astroliftAuditEvents: Array<AstroliftAuditEvent>;
  astroliftAuditEventsPage: AstroliftAuditEventPage;
  astroliftAuditRetention: AstroliftAuditRetention;
  astroliftAvailableRepos: AstroliftRemoteRepoList;
  astroliftBudgets: Array<AstroliftBudget>;
  astroliftClusterBootstrapPlan?: Maybe<AstroliftClusterBootstrapPlan>;
  astroliftClusterCount: Scalars['Int']['output'];
  astroliftClusterHealth?: Maybe<AstroliftClusterHealth>;
  astroliftClusterLifecycleAudit: Array<AstroliftClusterLifecycleAuditEntry>;
  astroliftClusterWorkloadHealth: Array<AstroliftClusterWorkloadHealth>;
  astroliftClusters: Array<AstroliftTenantCluster>;
  astroliftCommandRuns: Array<AstroliftCommandRun>;
  astroliftContainers: Array<AstroliftContainer>;
  astroliftCostByBinding: AstroliftCostAttribution;
  astroliftCostForecast: AstroliftCostForecast;
  astroliftCostSnapshots: Array<AstroliftCostSnapshot>;
  astroliftCostTrend: Array<AstroliftCostTrendPoint>;
  astroliftDeployment?: Maybe<AstroliftDeployment>;
  astroliftDeploymentApprovalHistory: Array<AstroliftDeploymentApprovalHistoryEntry>;
  astroliftDeploymentLog: Array<AstroliftDeploymentLogEntry>;
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
  astroliftDeployments: Array<AstroliftDeployment>;
  astroliftEnvironments: Array<AstroliftAppEnvironment>;
  astroliftEvents: Array<AstroliftEvent>;
  astroliftEventsPage: AstroliftEventPage;
  astroliftIdentityProviders: Array<AstroliftIdentityProvider>;
  astroliftInvitations: Array<AstroliftInvitation>;
  astroliftManagedDomains: Array<AstroliftManagedDomain>;
  astroliftManagedServices: Array<AstroliftManagedService>;
  astroliftMembers: Array<AstroliftMember>;
  astroliftMyApps: Array<AstroliftRegisteredApp>;
  astroliftMyConnectedAccounts: Array<AstroliftMyConnectedAccount>;
  astroliftMyNotifications: Array<AstroliftNotification>;
  astroliftMyPermissions: Array<Scalars['String']['output']>;
  astroliftMyProfile?: Maybe<AstroliftMyProfile>;
  astroliftNavTree?: Maybe<AstroliftNavTree>;
  astroliftOrganization?: Maybe<AstroliftOrganization>;
  astroliftOrganizationAllowlistDomains: Array<AstroliftOrganizationAllowlistedDomain>;
  astroliftOrganizations: Array<AstroliftOrganization>;
  astroliftPlatformApiUrl: Scalars['String']['output'];
  astroliftPolicies: Array<AstroliftPolicy>;
  astroliftPreviewEnvironments: Array<AstroliftPreviewEnvironment>;
  astroliftProjects: Array<AstroliftProject>;
  astroliftProviderPlugins: Array<AstroliftProviderPlugin>;
  astroliftQuotas: Array<AstroliftQuota>;
  astroliftRecentActivity: AstroliftActivityPage;
  astroliftRecentClusterWorkflows: Array<AstroliftClusterWorkflowRun>;
  astroliftRenderedManifest?: Maybe<AstroliftRenderedManifest>;
  astroliftRoleBindings: Array<AstroliftRoleBinding>;
  astroliftRoles: Array<AstroliftRole>;
  astroliftScheduledJobRuns: Array<AstroliftScheduledJobRun>;
  astroliftSecretBundles: Array<AstroliftSecretBundle>;
  astroliftSourceConnections: Array<AstroliftSourceConnection>;
  astroliftSourceFile: AstroliftSourceFile;
  astroliftSshDeployKeys: Array<AstroliftSshDeployKey>;
  astroliftTeams: Array<AstroliftTeam>;
  astroliftWebhookDeliveries: Array<AstroliftWebhookDelivery>;
  astroliftWebhookSubscriptions: Array<AstroliftWebhookSubscription>;
  astroliftWorkflowRuns: Array<AstroliftWorkflowRun>;
  astroliftWorkload?: Maybe<AstroliftWorkload>;
  astroliftWorkloadPodStatusBreakdown: Array<AstroliftWorkloadPodStatusBucket>;
  astroliftWorkloads: Array<AstroliftWorkload>;
  /** Query mutation audit logs. Admin only. */
  auditLogs: Array<AuditLogEntry>;
  /** List all effective permissions for a user, with the groups that grant each one. */
  effectivePermissions: Array<PermissionEntry>;
  employees: EmployeesConnection;
  members: Array<OrganizationMemberType>;
  organization?: Maybe<OrganizationType>;
  organizations: Array<OrganizationType>;
  /** Compare effective permissions between two users. */
  permissionCompare?: Maybe<PermissionComparison>;
  /** Diagnose why a user can or can't perform a specific permission. */
  permissionDiagnose?: Maybe<PermissionDiagnosis>;
  /** Get a workflow definition by slug. */
  workflowDefinition?: Maybe<WorkflowDefinitionType>;
  /** List all workflow definitions. */
  workflowDefinitions: Array<WorkflowDefinitionType>;
  /** Get a workflow instance by ID. */
  workflowInstance?: Maybe<WorkflowInstanceType>;
  /** List workflow instances for a specific object. */
  workflowInstances: Array<WorkflowInstanceType>;
};


export type QueryAstroliftAlertEventsArgs = {
  limit?: Scalars['Int']['input'];
  ruleId?: InputMaybe<Scalars['GUID']['input']>;
  unresolvedOnly?: Scalars['Boolean']['input'];
};


export type QueryAstroliftAlertRulesArgs = {
  activeOnly?: Scalars['Boolean']['input'];
  target?: InputMaybe<Scalars['String']['input']>;
  targetId?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppArgs = {
  slug: Scalars['String']['input'];
};


export type QueryAstroliftAppCertificatesArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppCountForClusterArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftAppDeployTokensArgs = {
  appSlug: Scalars['String']['input'];
};


export type QueryAstroliftAppDnsRecordsArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppDomainsArgs = {
  appSlug: Scalars['String']['input'];
};


export type QueryAstroliftAppGoldenSignalsArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  rangeSeconds?: InputMaybe<Scalars['Int']['input']>;
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppIdentityBindingArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppMetricsArgs = {
  appSlug: Scalars['String']['input'];
  timeRange?: Scalars['String']['input'];
};


export type QueryAstroliftAppPodsArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppSecretBundleAttachmentsArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppSecretsArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppStatusCodeBreakdownArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  rangeSeconds?: InputMaybe<Scalars['Int']['input']>;
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppTeamAccessesArgs = {
  appSlug: Scalars['String']['input'];
};


export type QueryAstroliftAuditEventsArgs = {
  action?: InputMaybe<Scalars['String']['input']>;
  actorId?: InputMaybe<Scalars['String']['input']>;
  createdAtGte?: InputMaybe<Scalars['DateTime']['input']>;
  createdAtLte?: InputMaybe<Scalars['DateTime']['input']>;
  decision?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftAuditEventsPageArgs = {
  action?: InputMaybe<Scalars['String']['input']>;
  actorId?: InputMaybe<Scalars['String']['input']>;
  after?: InputMaybe<Scalars['String']['input']>;
  createdAtGte?: InputMaybe<Scalars['DateTime']['input']>;
  createdAtLte?: InputMaybe<Scalars['DateTime']['input']>;
  decision?: InputMaybe<Scalars['String']['input']>;
  includeTotal?: Scalars['Boolean']['input'];
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftAvailableReposArgs = {
  connectionId: Scalars['String']['input'];
  limit?: Scalars['Int']['input'];
  search?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftClusterBootstrapPlanArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftClusterHealthArgs = {
  clusterId: Scalars['GUID']['input'];
  eventLimit?: Scalars['Int']['input'];
};


export type QueryAstroliftClusterLifecycleAuditArgs = {
  clusterId: Scalars['GUID']['input'];
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftClusterWorkloadHealthArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftCommandRunsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftContainersArgs = {
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftCostByBindingArgs = {
  days?: InputMaybe<Scalars['Int']['input']>;
  registeredAppSlug?: InputMaybe<Scalars['String']['input']>;
  window?: InputMaybe<CostWindow>;
};


export type QueryAstroliftCostSnapshotsArgs = {
  days?: InputMaybe<Scalars['Int']['input']>;
  limit?: Scalars['Int']['input'];
  window?: InputMaybe<CostWindow>;
};


export type QueryAstroliftCostTrendArgs = {
  days?: InputMaybe<Scalars['Int']['input']>;
  window?: InputMaybe<CostWindow>;
};


export type QueryAstroliftDeploymentArgs = {
  id: Scalars['String']['input'];
};


export type QueryAstroliftDeploymentApprovalHistoryArgs = {
  deploymentId: Scalars['String']['input'];
};


export type QueryAstroliftDeploymentLogArgs = {
  deploymentId: Scalars['String']['input'];
};


export type QueryAstroliftDeploymentMetricsArgs = {
  windowDays?: Scalars['Int']['input'];
};


export type QueryAstroliftDeploymentsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftEnvironmentsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftEventsArgs = {
  eventType?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftEventsPageArgs = {
  after?: InputMaybe<Scalars['String']['input']>;
  eventType?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftInvitationsArgs = {
  status?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftManagedServicesArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMyNotificationsArgs = {
  limit?: Scalars['Int']['input'];
  unreadOnly?: Scalars['Boolean']['input'];
};


export type QueryAstroliftOrganizationArgs = {
  slug: Scalars['String']['input'];
};


export type QueryAstroliftPreviewEnvironmentsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftRecentActivityArgs = {
  cursor?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftRecentClusterWorkflowsArgs = {
  clusterId: Scalars['GUID']['input'];
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftRenderedManifestArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  imageTag?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftScheduledJobRunsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftSourceFileArgs = {
  connectionId: Scalars['String']['input'];
  path: Scalars['String']['input'];
  ref: Scalars['String']['input'];
  repoFullName: Scalars['String']['input'];
};


export type QueryAstroliftSshDeployKeysArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftWebhookDeliveriesArgs = {
  limit?: Scalars['Int']['input'];
  subscriptionId: Scalars['GUID']['input'];
};


export type QueryAstroliftWebhookSubscriptionsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftWorkflowRunsArgs = {
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftWorkloadArgs = {
  appSlug: Scalars['String']['input'];
  slug: Scalars['String']['input'];
};


export type QueryAstroliftWorkloadPodStatusBreakdownArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  workloadSlug: Scalars['String']['input'];
};


export type QueryAstroliftWorkloadsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAuditLogsArgs = {
  limit?: Scalars['Int']['input'];
  operation?: InputMaybe<Scalars['String']['input']>;
  userId?: InputMaybe<Scalars['String']['input']>;
};


export type QueryEffectivePermissionsArgs = {
  userId: Scalars['ID']['input'];
};


export type QueryEmployeesArgs = {
  departmentsDepartmentNameIcontains?: InputMaybe<Scalars['String']['input']>;
  departmentsPositionNameIcontains?: InputMaybe<Scalars['String']['input']>;
  first?: InputMaybe<Scalars['Int']['input']>;
  offset?: InputMaybe<Scalars['Int']['input']>;
  search?: InputMaybe<Scalars['String']['input']>;
  showDeactivated?: InputMaybe<Scalars['Boolean']['input']>;
};


export type QueryOrganizationArgs = {
  id: Scalars['ID']['input'];
};


export type QueryOrganizationsArgs = {
  query?: InputMaybe<Scalars['String']['input']>;
};


export type QueryPermissionCompareArgs = {
  userIdA: Scalars['ID']['input'];
  userIdB: Scalars['ID']['input'];
};


export type QueryPermissionDiagnoseArgs = {
  permission: Scalars['String']['input'];
  userId: Scalars['ID']['input'];
};


export type QueryWorkflowDefinitionArgs = {
  slug: Scalars['String']['input'];
};


export type QueryWorkflowDefinitionsArgs = {
  includeDisabled?: Scalars['Boolean']['input'];
  modelLabel?: InputMaybe<Scalars['String']['input']>;
};


export type QueryWorkflowInstanceArgs = {
  id: Scalars['ID']['input'];
};


export type QueryWorkflowInstancesArgs = {
  modelLabel?: InputMaybe<Scalars['String']['input']>;
  objectId: Scalars['Int']['input'];
};

export type RecheckDomainValidationInput = {
  id: Scalars['GUID']['input'];
};

export type RecordClusterBootstrapRunInput = {
  chartVersion: Scalars['String']['input'];
  cliVersion: Scalars['String']['input'];
  clusterSlug: Scalars['String']['input'];
  endedAt: Scalars['DateTime']['input'];
  errorMessage: InputMaybe<Scalars['String']['input']>;
  hostInfo: Scalars['JSON']['input'];
  installedReleases: Scalars['JSON']['input'];
  startedAt: Scalars['DateTime']['input'];
  status: Scalars['String']['input'];
};

export type RefreshClusterManagementInputType = {
  clusterId: Scalars['GUID']['input'];
  forcePreflight: Scalars['Boolean']['input'];
};

export type RegisterAppInput = {
  approverTeamId: InputMaybe<Scalars['GUID']['input']>;
  approverUserIds: InputMaybe<Array<Scalars['String']['input']>>;
  cronExpression: InputMaybe<Scalars['String']['input']>;
  defaultBranch: InputMaybe<Scalars['String']['input']>;
  deployBranch: InputMaybe<Scalars['String']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  manifestPath: InputMaybe<Scalars['String']['input']>;
  manifestRaw: InputMaybe<Scalars['String']['input']>;
  minimumApprovals: InputMaybe<Scalars['Int']['input']>;
  name: Scalars['String']['input'];
  projectId: Scalars['GUID']['input'];
  requiresApproval: InputMaybe<Scalars['Boolean']['input']>;
  slug: Scalars['String']['input'];
  sourceKind: Scalars['String']['input'];
  sourceRepo: Scalars['String']['input'];
  sourceUrl: InputMaybe<Scalars['String']['input']>;
  triggerMode: InputMaybe<Scalars['String']['input']>;
};

export type RegisterTenantClusterInput = {
  authConfig: InputMaybe<Scalars['JSON']['input']>;
  authMethod: Scalars['String']['input'];
  caCert: InputMaybe<Scalars['String']['input']>;
  endpoint: InputMaybe<Scalars['String']['input']>;
  ingressClass: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
  organizationScoped: Scalars['Boolean']['input'];
  providerConfig: InputMaybe<Scalars['JSON']['input']>;
  providerPluginSlug: Scalars['String']['input'];
  region: InputMaybe<Scalars['String']['input']>;
  slug: Scalars['String']['input'];
};

export type RejectByTokenInput = {
  reason: InputMaybe<Scalars['String']['input']>;
  token: Scalars['String']['input'];
};

export type RemoveAppDomainInput = {
  id: Scalars['GUID']['input'];
};

export type RemoveOrganizationAllowlistDomainInput = {
  id: Scalars['GUID']['input'];
};

export type RestartWorkloadInput = {
  workloadId: Scalars['GUID']['input'];
};

export type ResyncManifestFromRepoInput = {
  appSlug: Scalars['String']['input'];
};

export type ResyncManifestPayload = {
  envKeysChanged: Scalars['Int']['output'];
  managedServicesAdded: Array<Scalars['String']['output']>;
  managedServicesRemoved: Array<Scalars['String']['output']>;
  schedulesChanged: Scalars['Int']['output'];
  summary: Scalars['String']['output'];
  syncState: Scalars['String']['output'];
  workloadsAdded: Array<Scalars['String']['output']>;
  workloadsChanged: Array<Scalars['String']['output']>;
  workloadsRemoved: Array<Scalars['String']['output']>;
};

export type ResyncManifestPayloadMutationResult = {
  data?: Maybe<ResyncManifestPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type RevealAppSecretInput = {
  appSlug: Scalars['String']['input'];
  secretId: Scalars['String']['input'];
};

export type RevokeApiTokenInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeAppCertificateInput = {
  customDomainId: Scalars['GUID']['input'];
};

export type RevokeDeployTokenInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeInvitationInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeRoleBindingInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeTeamAccessInput = {
  appId: Scalars['GUID']['input'];
  teamId: Scalars['GUID']['input'];
};

export type RotateDeployTokenInput = {
  id: Scalars['GUID']['input'];
};

export type RotateOutboundWebhookSecretInput = {
  id: Scalars['GUID']['input'];
};

export type RotateSecretBundleInput = {
  id: Scalars['GUID']['input'];
};

export type RotateWebhookSecretInput = {
  connectionId: Scalars['GUID']['input'];
};

export type RunJobOnceInput = {
  appSlug: Scalars['String']['input'];
  environmentName: Scalars['String']['input'];
  jobSlug: Scalars['String']['input'];
};

export type ScaleWorkloadInput = {
  replicas: Scalars['Int']['input'];
  workloadId: Scalars['GUID']['input'];
};

export type SetActiveIdentityProviderInput = {
  id: Scalars['GUID']['input'];
};

export type SetAppSecretInput = {
  appSlug: Scalars['String']['input'];
  key: Scalars['String']['input'];
  value: Scalars['String']['input'];
};

export type SetAppSubdomainInput = {
  id: Scalars['GUID']['input'];
  subdomain: Scalars['String']['input'];
};

export type SharedDirectoryType = {
  directoryCount: Scalars['Int']['output'];
  fileCount: Scalars['Int']['output'];
};

export type SoftDeleteAppInput = {
  id: Scalars['GUID']['input'];
};

export type SoftDeleteByGuidInput = {
  id: Scalars['GUID']['input'];
};

export type SoftDeleteManagedDomainInput = {
  id: Scalars['GUID']['input'];
};

export type Softdeletepayload = {
  deleted: Scalars['Boolean']['output'];
  id: Scalars['GUID']['output'];
};

export type SoftdeletepayloadMutationResult = {
  data?: Maybe<Softdeletepayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type StartDeploymentInput = {
  appSlug: Scalars['String']['input'];
  branch: InputMaybe<Scalars['String']['input']>;
  ciActorKind: InputMaybe<Scalars['String']['input']>;
  ciProvider: InputMaybe<Scalars['String']['input']>;
  ciRunUrl: InputMaybe<Scalars['String']['input']>;
  commitAuthor: InputMaybe<Scalars['String']['input']>;
  commitMessage: InputMaybe<Scalars['String']['input']>;
  commitSha: InputMaybe<Scalars['String']['input']>;
  environmentName: Scalars['String']['input'];
  imageDigest: InputMaybe<Scalars['String']['input']>;
  imageTag: Scalars['String']['input'];
  triggerKind: Scalars['String']['input'];
  workloadSlug: InputMaybe<Scalars['String']['input']>;
};

export type StartWorkflowResult = {
  errors: Array<ValidationError>;
  instanceId?: Maybe<Scalars['ID']['output']>;
  ok: Scalars['Boolean']['output'];
};

export type Subscription = {
  astroliftDeploymentLifecycleStream: AstroliftDeploymentLifecycleEvent;
  astroliftOnAppLog: AstroliftAppLogLine;
  formSubmissionReceived: Scalars['String']['output'];
  notificationReceived: Scalars['String']['output'];
};


export type SubscriptionAstroliftDeploymentLifecycleStreamArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type SubscriptionAstroliftOnAppLogArgs = {
  appSlug: Scalars['String']['input'];
  container?: InputMaybe<Scalars['String']['input']>;
  follow?: Scalars['Boolean']['input'];
  podName: Scalars['String']['input'];
  tailLines?: Scalars['Int']['input'];
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
};


export type SubscriptionFormSubmissionReceivedArgs = {
  slug: Scalars['String']['input'];
};

export type SwitchUserResult = {
  user?: Maybe<UserType>;
};

export type SyncManifestFromRepoInput = {
  id: Scalars['GUID']['input'];
};

export type TearDownAppInput = {
  deleteData: Scalars['Boolean']['input'];
  forceDestroy: Scalars['Boolean']['input'];
  id: Scalars['GUID']['input'];
};

export type TearDownPreviewInputGql = {
  id: Scalars['GUID']['input'];
};

export type TestNotificationInput = {
  id: Scalars['GUID']['input'];
  message: InputMaybe<Scalars['String']['input']>;
};

export type TestWebhookInput = {
  id: Scalars['GUID']['input'];
};

export type TransferAppInput = {
  appId: Scalars['GUID']['input'];
  targetProjectId: InputMaybe<Scalars['GUID']['input']>;
  targetTeamId: InputMaybe<Scalars['GUID']['input']>;
};

export type TransitionLogEntry = {
  fromState: Scalars['String']['output'];
  note: Scalars['String']['output'];
  timestamp: Scalars['DateTime']['output'];
  toState: Scalars['String']['output'];
  username?: Maybe<Scalars['String']['output']>;
};

export type TriggerDeployWorkflowInput = {
  appSlug: Scalars['String']['input'];
  branch: InputMaybe<Scalars['String']['input']>;
};

export type UnregisterTenantClusterInput = {
  id: Scalars['GUID']['input'];
};

export type UpdateAlertRuleInput = {
  id: Scalars['GUID']['input'];
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  notifyChannels: InputMaybe<Scalars['JSON']['input']>;
  predicate: InputMaybe<Scalars['JSON']['input']>;
  severity: InputMaybe<Scalars['String']['input']>;
};

export type UpdateAppInput = {
  approverTeamId: InputMaybe<Scalars['GUID']['input']>;
  approverUserIds: InputMaybe<Array<Scalars['String']['input']>>;
  cronExpression: InputMaybe<Scalars['String']['input']>;
  cronPaused: InputMaybe<Scalars['Boolean']['input']>;
  defaultBranch: InputMaybe<Scalars['String']['input']>;
  deployBranch: InputMaybe<Scalars['String']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  manifestPath: InputMaybe<Scalars['String']['input']>;
  minimumApprovals: InputMaybe<Scalars['Int']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  previewEnabled: InputMaybe<Scalars['Boolean']['input']>;
  requiresApproval: InputMaybe<Scalars['Boolean']['input']>;
  sourceUrl: InputMaybe<Scalars['String']['input']>;
  triggerMode: InputMaybe<Scalars['String']['input']>;
};

export type UpdateIdentityProviderInput = {
  clientId: InputMaybe<Scalars['String']['input']>;
  clientSecretRef: InputMaybe<Scalars['String']['input']>;
  config: InputMaybe<Scalars['JSON']['input']>;
  displayName: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  metadataUrl: InputMaybe<Scalars['String']['input']>;
  oidcDiscoveryUrl: InputMaybe<Scalars['String']['input']>;
};

export type UpdateManagedDomainInput = {
  defaultFor: InputMaybe<Scalars['String']['input']>;
  dnsConfig: InputMaybe<Scalars['JSON']['input']>;
  id: Scalars['GUID']['input'];
  isWildcardManaged: InputMaybe<Scalars['Boolean']['input']>;
};

export type UpdateManagedServiceInput = {
  config: InputMaybe<Scalars['JSON']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
};

export type UpdateManifestInput = {
  id: Scalars['GUID']['input'];
  rawManifest: Scalars['String']['input'];
};

export type UpdateMyProfileInput = {
  email: InputMaybe<Scalars['String']['input']>;
  firstName: InputMaybe<Scalars['String']['input']>;
  lastName: InputMaybe<Scalars['String']['input']>;
};

export type UpdateOrganizationInput = {
  allowUserProfileEdit: InputMaybe<Scalars['Boolean']['input']>;
  auditLogRetentionDays: InputMaybe<Scalars['Int']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
  website: InputMaybe<Scalars['String']['input']>;
};

export type UpdatePolicyInput = {
  actionPattern: InputMaybe<Scalars['String']['input']>;
  actorPattern: InputMaybe<Scalars['JSON']['input']>;
  conditions: InputMaybe<Scalars['JSON']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  effect: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
  resourcePattern: InputMaybe<Scalars['JSON']['input']>;
};

export type UpdateProjectInput = {
  description: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
};

export type UpdateRoleInput = {
  description: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
  permissions: InputMaybe<Array<Scalars['String']['input']>>;
};

export type UpdateSecurityPolicyInput = {
  appSlug: Scalars['String']['input'];
  blockOnCriticalCves: Scalars['Boolean']['input'];
  blockOnHighCveThreshold: InputMaybe<Scalars['Int']['input']>;
  blockOnMissingSignature: Scalars['Boolean']['input'];
};

export type UpdateSourceConnectionInput = {
  displayName: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  repoVisibilityScopes: InputMaybe<Array<Scalars['String']['input']>>;
  rotateSecretPlaintext: InputMaybe<Scalars['String']['input']>;
};

export type UpdateTeamInput = {
  description: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
};

export type UpdateTenantClusterInput = {
  endpoint: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  ingressClass: InputMaybe<Scalars['String']['input']>;
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  region: InputMaybe<Scalars['String']['input']>;
};

export type UpdateWebhookSubscriptionInput = {
  events: InputMaybe<Array<Scalars['String']['input']>>;
  format: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  url: InputMaybe<Scalars['String']['input']>;
};

export type UploadCustomDomainCertificateInput = {
  certificatePem: Scalars['String']['input'];
  id: Scalars['GUID']['input'];
  privateKeyPem: Scalars['String']['input'];
};

export type UploadLocation =
  | 'PUBLIC'
  | 'STATIC';

export type UploadType = {
  contentType?: Maybe<Scalars['String']['output']>;
  description?: Maybe<Scalars['String']['output']>;
  fileUrl?: Maybe<Scalars['String']['output']>;
  location?: Maybe<Scalars['String']['output']>;
  metadata?: Maybe<Scalars['JSON']['output']>;
  name?: Maybe<Scalars['String']['output']>;
  preSignedUrl?: Maybe<Scalars['String']['output']>;
  /** @deprecated Use file_url instead. */
  publicPermanentUrl?: Maybe<Scalars['String']['output']>;
  /** @deprecated Use file_url instead. */
  publicTransientUrl?: Maybe<Scalars['String']['output']>;
  targetGlobalId: Scalars['String']['output'];
};

export type UpsertOrganizationInput = {
  id: InputMaybe<Scalars['ID']['input']>;
  website: InputMaybe<Scalars['String']['input']>;
};

export type UpsertUserResult = {
  instance?: Maybe<UserType>;
};

export type UserInput = {
  firstName: InputMaybe<Scalars['String']['input']>;
  id: InputMaybe<Scalars['ID']['input']>;
  lastName: InputMaybe<Scalars['String']['input']>;
  profile: InputMaybe<ProfileInput>;
  username: Scalars['String']['input'];
};

export type UserType = {
  email: Scalars['String']['output'];
  employeeId?: Maybe<Scalars['String']['output']>;
  firstName: Scalars['String']['output'];
  isAnonymous: Scalars['Boolean']['output'];
  isNewUser: Scalars['Boolean']['output'];
  lastName: Scalars['String']['output'];
  memberships: Array<Scalars['JSON']['output']>;
  profile?: Maybe<ProfileType>;
  username: Scalars['String']['output'];
};

/** A field-level validation error. */
export type ValidationError = {
  field: Scalars['String']['output'];
  messages: Array<Scalars['String']['output']>;
};

export type WebhookSecretReveal = {
  plaintextSecret: Scalars['String']['output'];
  subscription: AstroliftWebhookSubscription;
};

export type WebhookSecretRevealMutationResult = {
  data?: Maybe<WebhookSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type WorkflowDefinitionType = {
  activeInstanceCount: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  description?: Maybe<Scalars['String']['output']>;
  instanceCount: Scalars['Int']['output'];
  isEnabled: Scalars['Boolean']['output'];
  modelLabel: Scalars['String']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  states: Scalars['JSON']['output'];
  transitions: Scalars['JSON']['output'];
};

export type WorkflowInstanceType = {
  availableTransitions: Array<AvailableTransition>;
  completedAt?: Maybe<Scalars['DateTime']['output']>;
  currentState: Scalars['String']['output'];
  history: Array<TransitionLogEntry>;
  isCompleted: Scalars['Boolean']['output'];
  objectId: Scalars['Int']['output'];
  startedAt: Scalars['DateTime']['output'];
  stateLabel: Scalars['String']['output'];
  workflowName: Scalars['String']['output'];
  workflowSlug: Scalars['String']['output'];
};
