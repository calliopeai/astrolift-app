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
  /** Represents NULL values */
  Void: { input: any; output: any; }
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

export type AddEmailSuppressionEntryInput = {
  address: Scalars['String']['input'];
  managedServiceId: Scalars['GUID']['input'];
  note: Scalars['String']['input'];
  reason: Scalars['String']['input'];
};

export type AddOrganizationAllowlistDomainInput = {
  defaultRoleSlug: InputMaybe<Scalars['String']['input']>;
  domain: Scalars['String']['input'];
  requiresReview: Scalars['Boolean']['input'];
};

export type AddWildcardDomainInput = {
  appSlug: Scalars['String']['input'];
  hostname: Scalars['String']['input'];
  sniCertRef: Scalars['String']['input'];
  validationMethod: Scalars['String']['input'];
};

export type AgentRunFamily =
  | 'SERVICE'
  | 'TASK';

export type AgentRunMode =
  | 'LOOP'
  | 'ONCE'
  | 'SCHEDULE'
  | 'TRIGGER';

export type AgentRunSpecInput = {
  replicas: InputMaybe<Scalars['Int']['input']>;
  runCronExpression: InputMaybe<Scalars['String']['input']>;
  runFamily: InputMaybe<AgentRunFamily>;
  runMaxParallel: InputMaybe<Scalars['Int']['input']>;
  runMode: InputMaybe<AgentRunMode>;
  runPaused: InputMaybe<Scalars['Boolean']['input']>;
  scaleDownCron: InputMaybe<Scalars['String']['input']>;
  scaleUpCron: InputMaybe<Scalars['String']['input']>;
  scheduledScaleTo: InputMaybe<Scalars['Int']['input']>;
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

export type ApproveSecretChangeInput = {
  proposalId: Scalars['GUID']['input'];
  reason: InputMaybe<Scalars['String']['input']>;
};

export type AppsListSortKey =
  | 'CREATED_DESC'
  | 'DEPLOYED_DESC'
  | 'NAME_ASC';

export type Appsecretmetadatapayload = {
  appSlug: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  key: Scalars['String']['output'];
  scope: Scalars['String']['output'];
  setAt?: Maybe<Scalars['DateTime']['output']>;
  setVia: Scalars['String']['output'];
};

export type AppsecretmetadatapayloadMutationResult = {
  data?: Maybe<Appsecretmetadatapayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type Appsecretwritepayload = {
  appSlug: Scalars['String']['output'];
  key: Scalars['String']['output'];
  pendingProposalId?: Maybe<Scalars['GUID']['output']>;
  rawManifestStaged: Scalars['String']['output'];
};

export type AppsecretwritepayloadMutationResult = {
  data?: Maybe<Appsecretwritepayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type ArchiveAppInput = {
  appSlug: Scalars['String']['input'];
};

export type ArchiveAppRegistryRepoInput = {
  appId: Scalars['GUID']['input'];
  archive: Scalars['Boolean']['input'];
};

export type AssertSessionInput = {
  assertion: Scalars['String']['input'];
  challenge: Scalars['String']['input'];
};

export type AssignAppToProjectInput = {
  appSlug: Scalars['String']['input'];
  projectGuid: InputMaybe<Scalars['GUID']['input']>;
};

export type AstroliftActiveSession = {
  attestationKind: Scalars['String']['output'];
  attestationTrustLevel: Scalars['String']['output'];
  attestedAt?: Maybe<Scalars['DateTime']['output']>;
  clientKind: Scalars['String']['output'];
  createdAt?: Maybe<Scalars['DateTime']['output']>;
  elevatedUntil?: Maybe<Scalars['DateTime']['output']>;
  elevationMethod?: Maybe<Scalars['String']['output']>;
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['String']['output'];
  ipAddress?: Maybe<Scalars['String']['output']>;
  isCurrent: Scalars['Boolean']['output'];
  label: Scalars['String']['output'];
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

export type AstroliftAgentDetail = {
  appSlug: Scalars['String']['output'];
  brief?: Maybe<AstroliftBrief>;
  dockerfilePath: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  imageRef: Scalars['String']['output'];
  name: Scalars['String']['output'];
  runCronExpression: Scalars['String']['output'];
  runFamily: Scalars['String']['output'];
  runMode: Scalars['String']['output'];
  runPaused: Scalars['Boolean']['output'];
  skills: Array<AstroliftAgentSkill>;
  slug: Scalars['String']['output'];
  sourceRepo: Scalars['String']['output'];
};

export type AstroliftAgentEnvironmentSpec = {
  agentType: Scalars['String']['output'];
  allowInstall: Scalars['Boolean']['output'];
  configBranch: Scalars['String']['output'];
  configManifestPath: Scalars['String']['output'];
  configRepo: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  envVars: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  imageTag: Scalars['String']['output'];
  name: Scalars['String']['output'];
  runtime: Scalars['String']['output'];
  secretRefs: Scalars['JSON']['output'];
  slug: Scalars['String']['output'];
  toolPreset: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
  vncEnabled: Scalars['Boolean']['output'];
};

export type AstroliftAgentEnvironmentSpecMutationResult = {
  data?: Maybe<AstroliftAgentEnvironmentSpec>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAgentListItem = {
  appSlug: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  lastRunAt?: Maybe<Scalars['DateTime']['output']>;
  lastRunStatus?: Maybe<Scalars['String']['output']>;
  name: Scalars['String']['output'];
  projectSlug: Scalars['String']['output'];
  runCronExpression: Scalars['String']['output'];
  runFamily: Scalars['String']['output'];
  runMode: Scalars['String']['output'];
  runPaused: Scalars['Boolean']['output'];
  runningCount: Scalars['Int']['output'];
  slug: Scalars['String']['output'];
  sourceRepo: Scalars['String']['output'];
  sourceUrl: Scalars['String']['output'];
};

export type AstroliftAgentLiveStatus = {
  appSlug: Scalars['String']['output'];
  isIdle: Scalars['Boolean']['output'];
  isPaused: Scalars['Boolean']['output'];
  lastRunAt?: Maybe<Scalars['DateTime']['output']>;
  lastRunStatus?: Maybe<Scalars['String']['output']>;
  nextScheduledAt?: Maybe<Scalars['DateTime']['output']>;
  runFamily: Scalars['String']['output'];
  runMode: Scalars['String']['output'];
  runningCount: Scalars['Int']['output'];
  workloadId: Scalars['GUID']['output'];
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftAgentRun = {
  createdAt: Scalars['DateTime']['output'];
  durationSeconds?: Maybe<Scalars['Int']['output']>;
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  input?: Maybe<Scalars['JSON']['output']>;
  k8sPodName: Scalars['String']['output'];
  output?: Maybe<Scalars['JSON']['output']>;
  reasoningTraceUrl: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  resultTtlHours: Scalars['Int']['output'];
  retryCount: Scalars['Int']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  toolCallsCount: Scalars['Int']['output'];
  triggerKind: Scalars['String']['output'];
  triggeredByUsername?: Maybe<Scalars['String']['output']>;
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftAgentRunSpec = {
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  replicas: Scalars['Int']['output'];
  runCronExpression: Scalars['String']['output'];
  runFamily: Scalars['String']['output'];
  runMaxParallel?: Maybe<Scalars['Int']['output']>;
  runMode: Scalars['String']['output'];
  runPaused: Scalars['Boolean']['output'];
  scaleDownCron: Scalars['String']['output'];
  scaleUpCron: Scalars['String']['output'];
  scheduledScaleTo?: Maybe<Scalars['Int']['output']>;
  slug: Scalars['String']['output'];
};

export type AstroliftAgentRunSpecMutationResult = {
  data?: Maybe<AstroliftAgentRunSpec>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAgentRuntime = {
  image: Scalars['String']['output'];
  name: Scalars['String']['output'];
};

export type AstroliftAgentSkill = {
  position: Scalars['Int']['output'];
  skill: AstroliftSkill;
  toolDefs: Array<AstroliftToolDef>;
};

export type AstroliftAgentTask = {
  callbackUrl: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  finishedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  result?: Maybe<Scalars['JSON']['output']>;
  snapshotUrl?: Maybe<Scalars['String']['output']>;
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  vncEnabled: Scalars['Boolean']['output'];
  vncUrl: Scalars['String']['output'];
};

export type AstroliftAgentTaskMutationResult = {
  data?: Maybe<AstroliftAgentTask>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAggregatedEvent = {
  count: Scalars['Int']['output'];
  eventType: Scalars['String']['output'];
  firstAt: Scalars['DateTime']['output'];
  lastAt: Scalars['DateTime']['output'];
  representative: AstroliftEvent;
  resourceId: Scalars['String']['output'];
  resourceKind: Scalars['String']['output'];
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

export type AstroliftAlertMute = {
  createdBy: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  reason: Scalars['String']['output'];
  ttlUntil: Scalars['DateTime']['output'];
};

export type AstroliftAlertRule = {
  activeMute?: Maybe<AstroliftAlertMute>;
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  managedServiceId?: Maybe<Scalars['GUID']['output']>;
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
  lastUsedAgent?: Maybe<Scalars['String']['output']>;
  lastUsedAt?: Maybe<Scalars['DateTime']['output']>;
  lastUsedIp?: Maybe<Scalars['String']['output']>;
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

export type AstroliftAppConfigDrift = {
  environmentName: Scalars['String']['output'];
  fields: Array<Scalars['String']['output']>;
  hasDrift: Scalars['Boolean']['output'];
  lastChecked: Scalars['DateTime']['output'];
};

export type AstroliftAppDeploymentSummary = {
  commitSha: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  imageTag: Scalars['String']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  triggeredBy: Scalars['String']['output'];
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
  certExpiresAt?: Maybe<Scalars['DateTime']['output']>;
  certIssuerSerial: Scalars['String']['output'];
  certObservabilityStatus: Scalars['String']['output'];
  certState: Scalars['String']['output'];
  certificateState: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  expectedCnameTarget: Scalars['String']['output'];
  hostname: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  isPlatformManagedZone: Scalars['Boolean']['output'];
  isWildcard: Scalars['Boolean']['output'];
  lastCertificateError: Scalars['String']['output'];
  lastCheckedAt?: Maybe<Scalars['DateTime']['output']>;
  lastValidationError: Scalars['String']['output'];
  pathRoutes: Array<AstroliftDomainPathRoute>;
  redirectRules: Array<AstroliftDomainRedirectRule>;
  registeredAppSlug: Scalars['String']['output'];
  requiredDnsRecords: Array<AstroliftAppDomainRequiredRecord>;
  sniCertRef: Scalars['String']['output'];
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

export type AstroliftAppEndpointMetric = {
  errorRateRatio: Scalars['Float']['output'];
  p50Ms?: Maybe<Scalars['Float']['output']>;
  p90Ms?: Maybe<Scalars['Float']['output']>;
  p99Ms?: Maybe<Scalars['Float']['output']>;
  requestRate: Scalars['Float']['output'];
  route: Scalars['String']['output'];
};

export type AstroliftAppEnvironment = {
  clusterId?: Maybe<Scalars['GUID']['output']>;
  clusterProviderPluginSlug?: Maybe<Scalars['String']['output']>;
  clusterSlug?: Maybe<Scalars['String']['output']>;
  createdAt: Scalars['DateTime']['output'];
  deploysPaused: Scalars['Boolean']['output'];
  domainZone?: Maybe<Scalars['String']['output']>;
  id: Scalars['GUID']['output'];
  ingressPaused: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  requiredApprovals: Scalars['Int']['output'];
  settings: Array<AstroliftEnvironmentSetting>;
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

export type AstroliftAppHealthPulse = {
  ageSeconds?: Maybe<Scalars['Int']['output']>;
  message: Scalars['String']['output'];
  status: AstroliftAppHealthPulseStatus;
};

export type AstroliftAppHealthPulseStatus =
  | 'DEGRADED'
  | 'NEVER'
  | 'OK'
  | 'STALE';

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

export type AstroliftAppListStatusFilter =
  | 'ALL'
  | 'DEGRADED'
  | 'NEVER_DEPLOYED'
  | 'OK'
  | 'STALE';

export type AstroliftAppLogExport = {
  byteCount: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  downloadUrl: Scalars['String']['output'];
  errorMessage: Scalars['String']['output'];
  expiresAt: Scalars['DateTime']['output'];
  format: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  rowCount: Scalars['Int']['output'];
  sha256: Scalars['String']['output'];
  status: Scalars['String']['output'];
  truncated: Scalars['Boolean']['output'];
};

export type AstroliftAppLogExportMutationResult = {
  data?: Maybe<AstroliftAppLogExport>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAppLogLine = {
  container: Scalars['String']['output'];
  message: Scalars['String']['output'];
  podName: Scalars['String']['output'];
  stream: Scalars['String']['output'];
  timestamp: Scalars['DateTime']['output'];
};

export type AstroliftAppLogPage = {
  historicalAvailable: Scalars['Boolean']['output'];
  items: Array<AstroliftAppLogQueryLine>;
  nextCursor: Scalars['String']['output'];
  reachedRetention: Scalars['Boolean']['output'];
  totalCount: Scalars['Int']['output'];
};

export type AstroliftAppLogQueryLine = {
  container: Scalars['String']['output'];
  level: Scalars['String']['output'];
  message: Scalars['String']['output'];
  podName: Scalars['String']['output'];
  stream: Scalars['String']['output'];
  timestamp: Scalars['String']['output'];
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
  recentErrorEvent?: Maybe<AstroliftAppPodEvent>;
  restarts: Scalars['Int']['output'];
  status: Scalars['String']['output'];
  workload: Scalars['String']['output'];
};

export type AstroliftAppPodEvent = {
  count: Scalars['Int']['output'];
  lastSeen: Scalars['String']['output'];
  message: Scalars['String']['output'];
  reason: Scalars['String']['output'];
  type: Scalars['String']['output'];
};

export type AstroliftAppReprovisionState = {
  elapsedSeconds?: Maybe<Scalars['Int']['output']>;
  needsReprovision: Scalars['Boolean']['output'];
  reason: Scalars['String']['output'];
  state: Scalars['String']['output'];
};

export type AstroliftAppSecret = {
  bundleSlug: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['String']['output'];
  isMasked: Scalars['Boolean']['output'];
  key: Scalars['String']['output'];
  lastEditedAt?: Maybe<Scalars['DateTime']['output']>;
  lastEditedBy?: Maybe<AstroliftSecretEditor>;
  managedServiceKind: Scalars['String']['output'];
  scope: Scalars['String']['output'];
  setVia: Scalars['String']['output'];
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

export type AstroliftAppSettingsLastModified = {
  deployStrategy?: Maybe<Scalars['DateTime']['output']>;
  deployTokens?: Maybe<Scalars['DateTime']['output']>;
  domains?: Maybe<Scalars['DateTime']['output']>;
  managedServices?: Maybe<Scalars['DateTime']['output']>;
  members?: Maybe<Scalars['DateTime']['output']>;
  observability?: Maybe<Scalars['DateTime']['output']>;
  secrets?: Maybe<Scalars['DateTime']['output']>;
  webhooks?: Maybe<Scalars['DateTime']['output']>;
};

export type AstroliftAppSourceKindFilter =
  | 'ALL'
  | 'BITBUCKET'
  | 'GITEA'
  | 'GITHUB'
  | 'GITLAB'
  | 'GIT_URL';

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

export type AstroliftAppTrace = {
  durationMs: Scalars['Float']['output'];
  rootOperation: Scalars['String']['output'];
  rootService: Scalars['String']['output'];
  spanCount: Scalars['Int']['output'];
  statusCode: Scalars['String']['output'];
  traceId: Scalars['String']['output'];
};

export type AstroliftAppUrlHealth = {
  lastChecked: Scalars['DateTime']['output'];
  latencyMs?: Maybe<Scalars['Int']['output']>;
  message: Scalars['String']['output'];
  status: Scalars['String']['output'];
  statusCode?: Maybe<Scalars['Int']['output']>;
  url: Scalars['String']['output'];
};

export type AstroliftApproverUser = {
  avatarUrl: Scalars['String']['output'];
  displayName: Scalars['String']['output'];
  email: Scalars['String']['output'];
  id: Scalars['String']['output'];
};

export type AstroliftAssembleBriefResult = {
  briefId?: Maybe<Scalars['GUID']['output']>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAssembleBriefResultMutationResult = {
  data?: Maybe<AstroliftAssembleBriefResult>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAttestationChallengePayload = {
  challenge: Scalars['String']['output'];
  expiresAt: Scalars['DateTime']['output'];
  kind: Scalars['String']['output'];
};

export type AstroliftAttestationChallengePayloadMutationResult = {
  data?: Maybe<AstroliftAttestationChallengePayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftAttestationResult = {
  attestedAt?: Maybe<Scalars['DateTime']['output']>;
  kind: Scalars['String']['output'];
  reason?: Maybe<Scalars['String']['output']>;
  trustLevel: Scalars['String']['output'];
};

export type AstroliftAttestationResultMutationResult = {
  data?: Maybe<AstroliftAttestationResult>;
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

export type AstroliftBrief = {
  config: Scalars['JSON']['output'];
  contentHash: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  storageKey: Scalars['String']['output'];
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

export type AstroliftBulkAssignTeamMemberRolesPayload = {
  alreadyAssignedCount: Scalars['Int']['output'];
  assignedCount: Scalars['Int']['output'];
  failedCount: Scalars['Int']['output'];
  results: Array<AstroliftBulkOpItemResult>;
};

export type AstroliftBulkAssignTeamMemberRolesPayloadMutationResult = {
  data?: Maybe<AstroliftBulkAssignTeamMemberRolesPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
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

export type AstroliftBulkOpItemResult = {
  alreadyExisted: Scalars['Boolean']['output'];
  errors: Array<MutationError>;
  id: Scalars['GUID']['output'];
  ok: Scalars['Boolean']['output'];
};

export type AstroliftBulkRevokeRoleBindingsPayload = {
  failedCount: Scalars['Int']['output'];
  results: Array<AstroliftBulkOpItemResult>;
  revokedCount: Scalars['Int']['output'];
};

export type AstroliftBulkRevokeRoleBindingsPayloadMutationResult = {
  data?: Maybe<AstroliftBulkRevokeRoleBindingsPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftCancelDeregisterPayload = {
  signalDelivered: Scalars['Boolean']['output'];
  workflowId: Scalars['String']['output'];
};

export type AstroliftCancelDeregisterPayloadMutationResult = {
  data?: Maybe<AstroliftCancelDeregisterPayload>;
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

export type AstroliftCiSecretValidation = {
  isCurrent?: Maybe<Scalars['Boolean']['output']>;
  isSet: Scalars['Boolean']['output'];
  secretName: Scalars['String']['output'];
  updatedAt: Scalars['String']['output'];
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

export type AstroliftClusterCertificate = {
  arn: Scalars['String']['output'];
  domainName: Scalars['String']['output'];
  name: Scalars['String']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftClusterCertificates = {
  certificates: Array<AstroliftClusterCertificate>;
  supported: Scalars['Boolean']['output'];
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

export type AstroliftClusterLiveState = {
  agentProvisioned: Scalars['Boolean']['output'];
  agentVersion: Scalars['String']['output'];
  appReadiness: Scalars['JSON']['output'];
  clusterId: Scalars['GUID']['output'];
  cpuUtilization?: Maybe<Scalars['Float']['output']>;
  heartbeatAgeSeconds?: Maybe<Scalars['Float']['output']>;
  heartbeatIntervalSeconds: Scalars['Int']['output'];
  ingressIps: Array<Scalars['String']['output']>;
  lastHeartbeatAt?: Maybe<Scalars['DateTime']['output']>;
  memoryUtilization?: Maybe<Scalars['Float']['output']>;
  nodeCount?: Maybe<Scalars['Int']['output']>;
  nodeReadyCount?: Maybe<Scalars['Int']['output']>;
  podTotal?: Maybe<Scalars['Int']['output']>;
  podsByNamespace: Scalars['JSON']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftClusterPodPhase = {
  count: Scalars['Int']['output'];
  namespace: Scalars['String']['output'];
  phase: Scalars['String']['output'];
};

export type AstroliftClusterPrometheusMetrics = {
  available: Scalars['Boolean']['output'];
  cpuUtilization?: Maybe<Scalars['Float']['output']>;
  deploymentReadyRatio?: Maybe<Scalars['Float']['output']>;
  memoryUtilization?: Maybe<Scalars['Float']['output']>;
  nodeCount?: Maybe<Scalars['Int']['output']>;
  podRunningRatio?: Maybe<Scalars['Float']['output']>;
  reason?: Maybe<Scalars['String']['output']>;
};

export type AstroliftClusterPrometheusRangeMetrics = {
  available: Scalars['Boolean']['output'];
  rangeSeconds: Scalars['Int']['output'];
  reason?: Maybe<Scalars['String']['output']>;
  series: Array<AstroliftClusterPrometheusRangeSeries>;
  stepSeconds: Scalars['Int']['output'];
};

export type AstroliftClusterPrometheusRangePoint = {
  ts: Scalars['Float']['output'];
  value: Scalars['Float']['output'];
};

export type AstroliftClusterPrometheusRangeSeries = {
  current?: Maybe<Scalars['Float']['output']>;
  label: Scalars['String']['output'];
  metric: Scalars['String']['output'];
  points: Array<AstroliftClusterPrometheusRangePoint>;
  unit: Scalars['String']['output'];
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

export type AstroliftCognitoUserPool = {
  domain: Scalars['String']['output'];
  name: Scalars['String']['output'];
  poolArn: Scalars['String']['output'];
  poolId: Scalars['String']['output'];
  region: Scalars['String']['output'];
};

export type AstroliftCognitoUserPoolClient = {
  clientId: Scalars['String']['output'];
  clientName: Scalars['String']['output'];
};

export type AstroliftCommandRun = {
  command: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  exitCode?: Maybe<Scalars['Int']['output']>;
  id: Scalars['GUID']['output'];
  invokedByUsername?: Maybe<Scalars['String']['output']>;
  logExcerpt: Scalars['String']['output'];
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
  livenessProbe?: Maybe<Scalars['JSON']['output']>;
  name: Scalars['String']['output'];
  port: Scalars['Int']['output'];
  readinessProbe?: Maybe<Scalars['JSON']['output']>;
  startupProbe?: Maybe<Scalars['JSON']['output']>;
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

export type AstroliftDeelevatePayload = {
  previouslyElevated: Scalars['Boolean']['output'];
};

export type AstroliftDeelevatePayloadMutationResult = {
  data?: Maybe<AstroliftDeelevatePayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftDeployToken = {
  createdAt: Scalars['DateTime']['output'];
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  isRevoked: Scalars['Boolean']['output'];
  last4: Scalars['String']['output'];
  lastRotatedAt?: Maybe<Scalars['DateTime']['output']>;
  lastUsedAgent: Scalars['String']['output'];
  lastUsedAt?: Maybe<Scalars['DateTime']['output']>;
  lastUsedIp: Scalars['String']['output'];
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
  commitAuthorAvatarUrl: Scalars['String']['output'];
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
  prNumber: Scalars['Int']['output'];
  prUrl: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  repoUrl: Scalars['String']['output'];
  requiredApproverCount: Scalars['Int']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  strategy: Scalars['String']['output'];
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

export type AstroliftDeploymentComparison = {
  baseSha: Scalars['String']['output'];
  compareUrl: Scalars['String']['output'];
  deploymentAId: Scalars['GUID']['output'];
  deploymentBId: Scalars['GUID']['output'];
  headSha: Scalars['String']['output'];
  imageDiffSummary: Scalars['String']['output'];
  manifestDiff: Array<AstroliftManifestDiffEntry>;
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

export type AstroliftDeregisterPreview = {
  appName: Scalars['String']['output'];
  appSlug: Scalars['String']['output'];
  deployTokens: Array<AstroliftDeregisterPreviewDeployToken>;
  identityRoles: Array<AstroliftDeregisterPreviewIdentityRole>;
  k8sObjects: Array<AstroliftDeregisterPreviewK8sObject>;
  managedServices: Array<AstroliftDeregisterPreviewManagedService>;
  registryRepoUri: Scalars['String']['output'];
  secretRefs: Array<AstroliftDeregisterPreviewSecretRef>;
  sourceWebhook?: Maybe<AstroliftDeregisterPreviewSourceWebhook>;
  totalResourceCount: Scalars['Int']['output'];
};

export type AstroliftDeregisterPreviewDeployToken = {
  environmentName?: Maybe<Scalars['String']['output']>;
  id: Scalars['GUID']['output'];
  last4: Scalars['String']['output'];
  name: Scalars['String']['output'];
};

export type AstroliftDeregisterPreviewIdentityRole = {
  clusterSlug: Scalars['String']['output'];
  kind: Scalars['String']['output'];
  roleArnOrPrincipal: Scalars['String']['output'];
};

export type AstroliftDeregisterPreviewK8sObject = {
  apiVersion: Scalars['String']['output'];
  clusterSlug: Scalars['String']['output'];
  kind: Scalars['String']['output'];
  name: Scalars['String']['output'];
  namespace: Scalars['String']['output'];
};

export type AstroliftDeregisterPreviewManagedService = {
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  name: Scalars['String']['output'];
  status: Scalars['String']['output'];
  variant: Scalars['String']['output'];
};

export type AstroliftDeregisterPreviewSecretRef = {
  bundleSlug: Scalars['String']['output'];
  clusterSlug?: Maybe<Scalars['String']['output']>;
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  prefix: Scalars['String']['output'];
};

export type AstroliftDeregisterPreviewSourceWebhook = {
  hookId: Scalars['String']['output'];
  installed: Scalars['Boolean']['output'];
  repo: Scalars['String']['output'];
};

export type AstroliftDeviceRegistration = {
  driver: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  label: Scalars['String']['output'];
  lastSeenAt?: Maybe<Scalars['DateTime']['output']>;
  platform: Scalars['String']['output'];
  registeredAt: Scalars['DateTime']['output'];
  tokenLast4: Scalars['String']['output'];
};

export type AstroliftDeviceRegistrationMutationResult = {
  data?: Maybe<AstroliftDeviceRegistration>;
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

export type AstroliftDiscoveredAgentManifest = {
  alreadyRegistered: Scalars['Boolean']['output'];
  manifestPath: Scalars['String']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  workloadKind: Scalars['String']['output'];
};

export type AstroliftDispatcherInstance = {
  capabilities: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  lastHeartbeat?: Maybe<Scalars['DateTime']['output']>;
  registeredAt: Scalars['DateTime']['output'];
  serviceUrl: Scalars['String']['output'];
};

export type AstroliftDnsZone = {
  configJson: Scalars['String']['output'];
  id: Scalars['String']['output'];
  name: Scalars['String']['output'];
  private: Scalars['Boolean']['output'];
};

export type AstroliftDnsZones = {
  supported: Scalars['Boolean']['output'];
  zones: Array<AstroliftDnsZone>;
};

export type AstroliftDomainPathRoute = {
  id: Scalars['GUID']['output'];
  pathPrefix: Scalars['String']['output'];
  priority: Scalars['Int']['output'];
  stripPrefix: Scalars['Boolean']['output'];
  targetPort: Scalars['Int']['output'];
  targetWorkloadSlug: Scalars['String']['output'];
};

export type AstroliftDomainRedirectRule = {
  destinationUrl: Scalars['String']['output'];
  httpStatus: Scalars['Int']['output'];
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  preserveQueryString: Scalars['Boolean']['output'];
  priority: Scalars['Int']['output'];
  sourcePattern: Scalars['String']['output'];
};

export type AstroliftElevatePayload = {
  elevatedUntil: Scalars['DateTime']['output'];
  method: Scalars['String']['output'];
  secondsRemaining: Scalars['Int']['output'];
};

export type AstroliftElevatePayloadMutationResult = {
  data?: Maybe<AstroliftElevatePayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftElevationStatus = {
  elevated: Scalars['Boolean']['output'];
  elevatedUntil?: Maybe<Scalars['DateTime']['output']>;
  method?: Maybe<Scalars['String']['output']>;
  requiredFor: Array<Scalars['String']['output']>;
  secondsRemaining: Scalars['Int']['output'];
};

export type AstroliftEmailAccountStatus = {
  bounceRatePct?: Maybe<Scalars['Float']['output']>;
  complaintRatePct?: Maybe<Scalars['Float']['output']>;
  productionAccess: Scalars['Boolean']['output'];
  reputationScore?: Maybe<Scalars['Float']['output']>;
  sendingEnabled: Scalars['Boolean']['output'];
};

export type AstroliftEmailDkimToken = {
  cnameHost: Scalars['String']['output'];
  cnameTarget: Scalars['String']['output'];
  token: Scalars['String']['output'];
};

export type AstroliftEmailDnsAuthCheck = {
  message: Scalars['String']['output'];
  outcome: Scalars['String']['output'];
  protocol: Scalars['String']['output'];
  records: Array<Scalars['String']['output']>;
};

export type AstroliftEmailDnsAuthStatus = {
  checkedAt: Scalars['DateTime']['output'];
  dkim: AstroliftEmailDnsAuthCheck;
  dmarc: AstroliftEmailDnsAuthCheck;
  identity: Scalars['String']['output'];
  overall: Scalars['String']['output'];
  spf: AstroliftEmailDnsAuthCheck;
};

export type AstroliftEmailEngagementMetrics = {
  bounceRatePct: Scalars['Float']['output'];
  clickRatePct: Scalars['Float']['output'];
  complaintRatePct: Scalars['Float']['output'];
  openRatePct: Scalars['Float']['output'];
  totalBounces: Scalars['Int']['output'];
  totalClicks: Scalars['Int']['output'];
  totalComplaints: Scalars['Int']['output'];
  totalDeliveries: Scalars['Int']['output'];
  totalOpens: Scalars['Int']['output'];
  totalSends: Scalars['Int']['output'];
  windowDays: Scalars['Int']['output'];
};

export type AstroliftEmailIdentityVerification = {
  dkimTokens: Array<AstroliftEmailDkimToken>;
  identity: Scalars['String']['output'];
  isDomain: Scalars['Boolean']['output'];
  status: Scalars['String']['output'];
  verificationToken: Scalars['String']['output'];
};

export type AstroliftEmailMessage = {
  eventKind: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  messageId: Scalars['String']['output'];
  metadata: Scalars['JSON']['output'];
  occurredAt: Scalars['DateTime']['output'];
  recipient: Scalars['String']['output'];
  subject: Scalars['String']['output'];
};

export type AstroliftEmailSendQuota = {
  max24HourSend: Scalars['Float']['output'];
  maxSendRate: Scalars['Float']['output'];
  sentLast24h: Scalars['Float']['output'];
};

export type AstroliftEmailServiceDetail = {
  accountStatus?: Maybe<AstroliftEmailAccountStatus>;
  dnsAuthStatus?: Maybe<AstroliftEmailDnsAuthStatus>;
  identity: Scalars['String']['output'];
  identityVerification?: Maybe<AstroliftEmailIdentityVerification>;
  managedServiceId: Scalars['GUID']['output'];
  pluginSlug: Scalars['String']['output'];
  quota?: Maybe<AstroliftEmailSendQuota>;
  region: Scalars['String']['output'];
  suppressionEntries: Array<AstroliftEmailSuppressionEntry>;
  unsupportedNotes: Array<Scalars['String']['output']>;
};

export type AstroliftEmailSuppressionEntry = {
  address: Scalars['String']['output'];
  detail: Scalars['String']['output'];
  reason: Scalars['String']['output'];
  suppressedAt: Scalars['DateTime']['output'];
};

export type AstroliftEmailTemplate = {
  createdAt?: Maybe<Scalars['DateTime']['output']>;
  htmlBody: Scalars['String']['output'];
  name: Scalars['String']['output'];
  subject: Scalars['String']['output'];
  textBody: Scalars['String']['output'];
};

export type AstroliftEmailTemplateMutationResult = {
  data?: Maybe<AstroliftEmailTemplate>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftEnrollmentQrPayload = {
  expiresAt: Scalars['DateTime']['output'];
  qrPayload: Scalars['String']['output'];
  qrSvg: Scalars['String']['output'];
  sessionGuid: Scalars['GUID']['output'];
  sessionId: Scalars['String']['output'];
  verificationUri: Scalars['String']['output'];
};

export type AstroliftEnrollmentQrPayloadMutationResult = {
  data?: Maybe<AstroliftEnrollmentQrPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftEnvironmentSetting = {
  id: Scalars['GUID']['output'];
  key: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type AstroliftEnvironmentSettingMutationResult = {
  data?: Maybe<AstroliftEnvironmentSetting>;
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
  resourceId: Scalars['String']['output'];
  resourceKind: Scalars['String']['output'];
  severity: Scalars['String']['output'];
  teamId?: Maybe<Scalars['String']['output']>;
};

export type AstroliftEventPage = {
  items: Array<AstroliftEvent>;
  nextCursor?: Maybe<Scalars['String']['output']>;
};

export type AstroliftExecutePromqlResult = {
  error: Scalars['String']['output'];
  ok: Scalars['Boolean']['output'];
  series: Array<AstroliftPromqlSeries>;
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

export type AstroliftForceRedeployPreview = {
  appSlug: Scalars['String']['output'];
  environmentName?: Maybe<Scalars['String']['output']>;
  inFlightDeployments: Array<AstroliftForceRedeployPreviewDeployment>;
};

export type AstroliftForceRedeployPreviewDeployment = {
  ciActorKind: Scalars['String']['output'];
  ciRunUrl: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  imageTag: Scalars['String']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  triggerKind: Scalars['String']['output'];
  triggeredByDisplay: Scalars['String']['output'];
  workloadSlug?: Maybe<Scalars['String']['output']>;
};

export type AstroliftFormDefinition = {
  createdAt: Scalars['DateTime']['output'];
  createdByUsername?: Maybe<Scalars['String']['output']>;
  description: Scalars['String']['output'];
  fieldConfig: Scalars['JSON']['output'];
  formType: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isPublic: Scalars['Boolean']['output'];
  logicRules: Scalars['JSON']['output'];
  name: Scalars['String']['output'];
  publishedAt?: Maybe<Scalars['DateTime']['output']>;
  schema: Scalars['JSON']['output'];
  scoring: Scalars['JSON']['output'];
  slug: Scalars['String']['output'];
  status: Scalars['String']['output'];
  submissionCount: Scalars['Int']['output'];
  updatedAt: Scalars['DateTime']['output'];
  updatedByUsername?: Maybe<Scalars['String']['output']>;
  version: Scalars['Int']['output'];
};

export type AstroliftFormDefinitionMutationResult = {
  data?: Maybe<AstroliftFormDefinition>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftFormSubmission = {
  createdAt: Scalars['DateTime']['output'];
  formName: Scalars['String']['output'];
  formSlug: Scalars['String']['output'];
  formVersion: Scalars['Int']['output'];
  id: Scalars['GUID']['output'];
  payload: Scalars['JSON']['output'];
  sourceIp?: Maybe<Scalars['String']['output']>;
  status: Scalars['String']['output'];
  submittedAt: Scalars['DateTime']['output'];
  submitterDisplayName: Scalars['String']['output'];
  submitterEmail: Scalars['String']['output'];
  userAgent: Scalars['String']['output'];
};

export type AstroliftFormSubmissionMutationResult = {
  data?: Maybe<AstroliftFormSubmission>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftHeartbeatSessionPayload = {
  id: Scalars['GUID']['output'];
  lastSeenAt?: Maybe<Scalars['DateTime']['output']>;
};

export type AstroliftHeartbeatSessionPayloadMutationResult = {
  data?: Maybe<AstroliftHeartbeatSessionPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftIdentityProvider = {
  activatedAt?: Maybe<Scalars['DateTime']['output']>;
  clientId: Scalars['String']['output'];
  config: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  isDefault: Scalars['Boolean']['output'];
  kind: Scalars['String']['output'];
  lastSwitchedByUsername?: Maybe<Scalars['String']['output']>;
  metadataUrl: Scalars['String']['output'];
  name: Scalars['String']['output'];
  oidcDiscoveryUrl: Scalars['String']['output'];
  organizationSlug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
  version: Scalars['Int']['output'];
};

export type AstroliftIdentityProviderMutationResult = {
  data?: Maybe<AstroliftIdentityProvider>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftImportSkillsResult = {
  importedSkills: Array<Scalars['String']['output']>;
  importedTools: Array<Scalars['String']['output']>;
  sourceRef: Scalars['String']['output'];
};

export type AstroliftImportSkillsResultMutationResult = {
  data?: Maybe<AstroliftImportSkillsResult>;
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
  invitedByAvatarUrl?: Maybe<Scalars['String']['output']>;
  invitedByDisplayName?: Maybe<Scalars['String']['output']>;
  invitedByEmail?: Maybe<Scalars['String']['output']>;
  invitedByUserId?: Maybe<Scalars['String']['output']>;
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

export type AstroliftJob = {
  containerImage: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  jobId: Scalars['String']['output'];
  name: Scalars['String']['output'];
  needs: Scalars['JSON']['output'];
  pipelineId: Scalars['GUID']['output'];
  runsOn: Scalars['String']['output'];
};

export type AstroliftJobRun = {
  createdAt: Scalars['DateTime']['output'];
  finishedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  job: AstroliftJob;
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  stepRuns: Array<AstroliftStepRun>;
};

export type AstroliftLaunchTaskResult = {
  ok: Scalars['Boolean']['output'];
  taskId?: Maybe<Scalars['GUID']['output']>;
};

export type AstroliftLaunchTaskResultMutationResult = {
  data?: Maybe<AstroliftLaunchTaskResult>;
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
  editableFields: Array<Scalars['String']['output']>;
  environmentName: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  lastActionAt?: Maybe<Scalars['DateTime']['output']>;
  lastActionKind: Scalars['String']['output'];
  name: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  status: Scalars['String']['output'];
  statusError: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
  variant: Scalars['String']['output'];
};

export type AstroliftManagedServiceConnection = {
  connectionSecretRef: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  keys: Array<AstroliftManagedServiceConnectionKey>;
  kind: Scalars['String']['output'];
  managedServiceId: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  revealedAt: Scalars['DateTime']['output'];
};

export type AstroliftManagedServiceConnectionKey = {
  isSecret: Scalars['Boolean']['output'];
  key: Scalars['String']['output'];
  value: Scalars['String']['output'];
};

export type AstroliftManagedServiceConnectionMutationResult = {
  data?: Maybe<AstroliftManagedServiceConnection>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftManagedServiceMetricSeries = {
  name: Scalars['String']['output'];
  samples: Array<AstroliftTimeSeriesPoint>;
  source: Scalars['String']['output'];
  unit: Scalars['String']['output'];
};

export type AstroliftManagedServiceMetrics = {
  kind: Scalars['String']['output'];
  managedServiceId: Scalars['ID']['output'];
  name: Scalars['String']['output'];
  rangeSeconds: Scalars['Int']['output'];
  series: Array<AstroliftManagedServiceMetricSeries>;
};

export type AstroliftManagedServiceMutationResult = {
  data?: Maybe<AstroliftManagedService>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftManagedServiceObject = {
  key: Scalars['String']['output'];
  lastModified?: Maybe<Scalars['DateTime']['output']>;
  sizeBytes: Scalars['Int']['output'];
};

export type AstroliftManagedServiceObjects = {
  cacheAgeSeconds?: Maybe<Scalars['Int']['output']>;
  kind: Scalars['String']['output'];
  managedServiceId: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  objects: Array<AstroliftManagedServiceObject>;
  truncated: Scalars['Boolean']['output'];
};

export type AstroliftManagedServiceQueueDepth = {
  depth: Scalars['Int']['output'];
  inFlight: Scalars['Int']['output'];
  kind: Scalars['String']['output'];
  managedServiceId: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  sampledAt?: Maybe<Scalars['DateTime']['output']>;
};

export type AstroliftManagedServiceTestEmailResult = {
  managedServiceId: Scalars['GUID']['output'];
  recipient: Scalars['String']['output'];
  sentAt: Scalars['DateTime']['output'];
  subject: Scalars['String']['output'];
  transport: Scalars['String']['output'];
};

export type AstroliftManagedServiceTestEmailResultMutationResult = {
  data?: Maybe<AstroliftManagedServiceTestEmailResult>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftManifestDiffEntry = {
  after: Scalars['JSON']['output'];
  before: Scalars['JSON']['output'];
  op: Scalars['String']['output'];
  path: Scalars['String']['output'];
};

export type AstroliftMarkOnboardingCompletePayload = {
  alreadyCompleted: Scalars['Boolean']['output'];
  organization: AstroliftOrganization;
};

export type AstroliftMarkOnboardingCompletePayloadMutationResult = {
  data?: Maybe<AstroliftMarkOnboardingCompletePayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftMe = {
  id: Scalars['String']['output'];
  modules: Array<AstroliftModuleEntitlement>;
  profile?: Maybe<AstroliftUserProfile>;
};

export type AstroliftMember = {
  createdAt: Scalars['DateTime']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  joinedAt?: Maybe<Scalars['DateTime']['output']>;
  lastActiveAt?: Maybe<Scalars['DateTime']['output']>;
  lastSeenAt?: Maybe<Scalars['DateTime']['output']>;
  lifecycle: Scalars['String']['output'];
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
  user: AstroliftUser;
};

export type AstroliftModuleEntitlement = {
  canCreate: Scalars['Boolean']['output'];
  canManage: Scalars['Boolean']['output'];
  canRun: Scalars['Boolean']['output'];
  canView: Scalars['Boolean']['output'];
  key: Scalars['String']['output'];
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
  timezone?: Maybe<Scalars['String']['output']>;
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

export type AstroliftNotificationPreference = {
  channel: Scalars['String']['output'];
  enabled: Scalars['Boolean']['output'];
  eventKind: Scalars['String']['output'];
  id?: Maybe<Scalars['GUID']['output']>;
};

export type AstroliftNotificationPreferenceMutationResult = {
  data?: Maybe<AstroliftNotificationPreference>;
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
  onboardingCompletedAt?: Maybe<Scalars['DateTime']['output']>;
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

export type AstroliftPipeline = {
  astroliftApp?: Maybe<AstroliftRegisteredAppStub>;
  createdAt: Scalars['DateTime']['output'];
  defaultBranch: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  repoUrl: Scalars['String']['output'];
  tomlPath: Scalars['String']['output'];
  triggers: Array<AstroliftTrigger>;
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftPipelineMutationResult = {
  data?: Maybe<AstroliftPipeline>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftPipelineRun = {
  createdAt: Scalars['DateTime']['output'];
  finishedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  jobRuns: Array<AstroliftJobRun>;
  runNumber: Scalars['Int']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  triggerActor: Scalars['String']['output'];
  triggerKind: Scalars['String']['output'];
  triggerRef: Scalars['String']['output'];
};

export type AstroliftPipelineRunMutationResult = {
  data?: Maybe<AstroliftPipelineRun>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftPodResourceUsage = {
  lastRestartAt?: Maybe<Scalars['DateTime']['output']>;
  podName: Scalars['String']['output'];
  rangeSeconds: Scalars['Int']['output'];
  restartCount: Scalars['Int']['output'];
  samples: Array<AstroliftPodResourceUsagePoint>;
};

export type AstroliftPodResourceUsagePoint = {
  cpuCores: Scalars['Float']['output'];
  memoryBytes: Scalars['Float']['output'];
  ts: Scalars['DateTime']['output'];
};

export type AstroliftPolicy = {
  actionPattern: Scalars['String']['output'];
  actorPattern: Scalars['JSON']['output'];
  conditions: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  createdByUsername?: Maybe<Scalars['String']['output']>;
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
  updatedByUsername?: Maybe<Scalars['String']['output']>;
  version: Scalars['Int']['output'];
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
  isManual: Scalars['Boolean']['output'];
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

export type AstroliftPromqlSeries = {
  metricLabels: Scalars['JSON']['output'];
  values: Array<AstroliftTimeSeriesPoint>;
};

export type AstroliftProviderPlugin = {
  capabilitiesManifest: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  isEnabled: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  version: Scalars['String']['output'];
};

export type AstroliftProviderRegion = {
  continent: Scalars['String']['output'];
  id: Scalars['String']['output'];
  label: Scalars['String']['output'];
};

export type AstroliftProvisioningProgress = {
  completed: Array<Scalars['String']['output']>;
  currentStep: Scalars['String']['output'];
  totalSteps: Array<Scalars['String']['output']>;
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
  pendingRequest?: Maybe<AstroliftQuotaIncreaseRequest>;
  resource: Scalars['String']['output'];
  scopeId: Scalars['String']['output'];
  scopeKind: Scalars['String']['output'];
  softLimit: Scalars['Float']['output'];
};

export type AstroliftQuotaIncreaseRequest = {
  createdAt: Scalars['DateTime']['output'];
  decidedAt?: Maybe<Scalars['DateTime']['output']>;
  decidedByDisplay?: Maybe<Scalars['String']['output']>;
  decisionNote: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  quotaId: Scalars['GUID']['output'];
  reason: Scalars['String']['output'];
  requestedByDisplay: Scalars['String']['output'];
  requestedFactor: Scalars['Float']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftQuotaIncreaseRequestMutationResult = {
  data?: Maybe<AstroliftQuotaIncreaseRequest>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRegisterAgentRepoResult = {
  agents: Array<AstroliftRegisteredAgent>;
};

export type AstroliftRegisterAgentRepoResultMutationResult = {
  data?: Maybe<AstroliftRegisterAgentRepoResult>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRegisteredAgent = {
  appId: Scalars['GUID']['output'];
  created: Scalars['Boolean']['output'];
  manifestPath: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftRegisteredApp = {
  activePreviewCount: Scalars['Int']['output'];
  approverTeamId?: Maybe<Scalars['GUID']['output']>;
  approverUserIds: Array<Scalars['String']['output']>;
  archivedAt?: Maybe<Scalars['DateTime']['output']>;
  archivedByEmail?: Maybe<Scalars['String']['output']>;
  buildArgs: Scalars['JSON']['output'];
  buildContext: Scalars['String']['output'];
  buildMode: Scalars['String']['output'];
  buildStrategy: Scalars['String']['output'];
  configDrift?: Maybe<AstroliftAppConfigDrift>;
  createdAt: Scalars['DateTime']['output'];
  cronExpression: Scalars['String']['output'];
  cronPaused: Scalars['Boolean']['output'];
  defaultBranch: Scalars['String']['output'];
  deletedAt?: Maybe<Scalars['DateTime']['output']>;
  deployBranch: Scalars['String']['output'];
  deployTokenLast4: Scalars['String']['output'];
  description: Scalars['String']['output'];
  dockerfilePath: Scalars['String']['output'];
  ecrPushRoleArn: Scalars['String']['output'];
  ecrRepoUri: Scalars['String']['output'];
  healthPulse?: Maybe<AstroliftAppHealthPulse>;
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  isArchived: Scalars['Boolean']['output'];
  k8sNamespace: Scalars['String']['output'];
  lastDeployedAt?: Maybe<Scalars['DateTime']['output']>;
  lastResyncAt?: Maybe<Scalars['DateTime']['output']>;
  lastSyncedHash: Scalars['String']['output'];
  latestDeployment?: Maybe<AstroliftAppDeploymentSummary>;
  logRetentionDays: Scalars['Int']['output'];
  managedHostname: Scalars['String']['output'];
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
  providerPluginSlug: Scalars['String']['output'];
  provisioningError: Scalars['String']['output'];
  provisioningProgress?: Maybe<AstroliftProvisioningProgress>;
  provisioningStatus: Scalars['String']['output'];
  rawManifest: Scalars['String']['output'];
  rawManifestStaged: Scalars['String']['output'];
  registryRepoUri: Scalars['String']['output'];
  reprovision: AstroliftAppReprovisionState;
  requiresApproval: Scalars['Boolean']['output'];
  retentionPolicies: Array<AstroliftRetentionPolicy>;
  securityPolicy: AstroliftSecurityPolicy;
  settingsLastModified?: Maybe<AstroliftAppSettingsLastModified>;
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
  version: Scalars['Int']['output'];
  viewerPermissions: Array<Scalars['String']['output']>;
  webhookDeploysPauseReason: Scalars['String']['output'];
  webhookDeploysPaused: Scalars['Boolean']['output'];
  webhookDeploysPausedAt?: Maybe<Scalars['DateTime']['output']>;
  webhookDeploysPausedByEmail?: Maybe<Scalars['String']['output']>;
};

export type AstroliftRegisteredAppMutationResult = {
  data?: Maybe<AstroliftRegisteredApp>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftRegisteredAppPage = {
  items: Array<AstroliftRegisteredApp>;
  nextCursor?: Maybe<Scalars['String']['output']>;
  totalCount: Scalars['Int']['output'];
};

export type AstroliftRegisteredAppStub = {
  id: Scalars['GUID']['output'];
  name: Scalars['String']['output'];
  slug: Scalars['String']['output'];
};

export type AstroliftReleaseNotes = {
  baseSha: Scalars['String']['output'];
  commits: Array<AstroliftReleaseNotesCommit>;
  compareUrl: Scalars['String']['output'];
  headSha: Scalars['String']['output'];
  pullRequests: Array<AstroliftReleaseNotesPr>;
};

export type AstroliftReleaseNotesCommit = {
  author: Scalars['String']['output'];
  isMerge: Scalars['Boolean']['output'];
  sha: Scalars['String']['output'];
  subject: Scalars['String']['output'];
};

export type AstroliftReleaseNotesPr = {
  author: Scalars['String']['output'];
  body: Scalars['String']['output'];
  mergedAt?: Maybe<Scalars['String']['output']>;
  number: Scalars['Int']['output'];
  prUrl: Scalars['String']['output'];
  title: Scalars['String']['output'];
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

export type AstroliftRetentionPolicy = {
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  registeredAppSlug: Scalars['String']['output'];
  retentionDays: Scalars['Int']['output'];
  signal: Scalars['String']['output'];
};

export type AstroliftRetentionPolicyMutationResult = {
  data?: Maybe<AstroliftRetentionPolicy>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
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

export type AstroliftRevokeAstroliftSessionPayload = {
  id: Scalars['GUID']['output'];
  revoked: Scalars['Boolean']['output'];
};

export type AstroliftRevokeAstroliftSessionPayloadMutationResult = {
  data?: Maybe<AstroliftRevokeAstroliftSessionPayload>;
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
  sourceScopeLabel: Scalars['String']['output'];
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

export type AstroliftScanAgentManifestsResult = {
  agents: Array<AstroliftDiscoveredAgentManifest>;
  error?: Maybe<Scalars['String']['output']>;
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

export type AstroliftSearchableUser = {
  avatarUrl: Scalars['String']['output'];
  displayLabel: Scalars['String']['output'];
  email: Scalars['String']['output'];
  expiresAt?: Maybe<Scalars['DateTime']['output']>;
  invitationId?: Maybe<Scalars['GUID']['output']>;
  invitationStatus?: Maybe<Scalars['String']['output']>;
  matchKind: Scalars['String']['output'];
  userId?: Maybe<Scalars['String']['output']>;
};

export type AstroliftSecretBundle = {
  backendRef: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  keyCount: Scalars['Int']['output'];
  lastKnownKeysAt?: Maybe<Scalars['DateTime']['output']>;
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

export type AstroliftSecretChangeApproval = {
  approverDisplayName: Scalars['String']['output'];
  approverUserId: Scalars['String']['output'];
  decidedAt: Scalars['DateTime']['output'];
  decision: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  reason: Scalars['String']['output'];
};

export type AstroliftSecretChangeProposal = {
  appliedAt?: Maybe<Scalars['DateTime']['output']>;
  applyError: Scalars['String']['output'];
  approvals: Array<AstroliftSecretChangeApproval>;
  approvalsCount: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  decidedAt?: Maybe<Scalars['DateTime']['output']>;
  environmentName: Scalars['String']['output'];
  expiresAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  op: Scalars['String']['output'];
  payload: Scalars['JSON']['output'];
  payloadDiff: Scalars['JSON']['output'];
  proposerDisplayName: Scalars['String']['output'];
  proposerUserId: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  requiredApproverCount: Scalars['Int']['output'];
  status: Scalars['String']['output'];
};

export type AstroliftSecretChangeProposalMutationResult = {
  data?: Maybe<AstroliftSecretChangeProposal>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftSecretEditor = {
  displayName: Scalars['String']['output'];
  id: Scalars['String']['output'];
  username: Scalars['String']['output'];
};

export type AstroliftSecretHistoryActor = {
  id: Scalars['String']['output'];
  username: Scalars['String']['output'];
};

export type AstroliftSecretHistoryEntry = {
  action: Scalars['String']['output'];
  actor: AstroliftSecretHistoryActor;
  errorCode: Scalars['String']['output'];
  sourceIp: Scalars['String']['output'];
  success: Scalars['Boolean']['output'];
  timestamp: Scalars['DateTime']['output'];
};

export type AstroliftSecurityPolicy = {
  blockOnCriticalCves: Scalars['Boolean']['output'];
  blockOnHighCveThreshold?: Maybe<Scalars['Int']['output']>;
  blockOnMissingSignature: Scalars['Boolean']['output'];
};

/** Install identity + capabilities handshake (#479). Returned by ``astroliftServerInfo``. Callable by unauthenticated clients so multi-install mobile / CLI / SDK callers can pick the right UI and gate commands before login. */
export type AstroliftServerInfo = {
  apiVersion: Scalars['String']['output'];
  authMethods: Array<Scalars['String']['output']>;
  capabilities: Array<Scalars['String']['output']>;
  featureFlags: Array<FeatureFlagInfo>;
  installId: Scalars['String']['output'];
  installLabel?: Maybe<Scalars['String']['output']>;
  installSlug: Scalars['String']['output'];
  region?: Maybe<Scalars['String']['output']>;
  /** Current server-side wall clock (UTC). Used by clients to detect clock drift. */
  serverTime: Scalars['DateTime']['output'];
  version: Scalars['String']['output'];
};

export type AstroliftSkill = {
  content: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  description: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  isActive: Scalars['Boolean']['output'];
  isGlobal: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
  skillVersion: Scalars['Int']['output'];
  slug: Scalars['String']['output'];
  updatedAt: Scalars['DateTime']['output'];
};

export type AstroliftSkillMutationResult = {
  data?: Maybe<AstroliftSkill>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftSourceConnection = {
  accountLogin: Scalars['String']['output'];
  apiBaseUrl: Scalars['String']['output'];
  appClientId: Scalars['String']['output'];
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
  needsClientId: Scalars['Boolean']['output'];
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

export type AstroliftStep = {
  createdAt: Scalars['DateTime']['output'];
  env: Scalars['JSON']['output'];
  id: Scalars['GUID']['output'];
  jobId: Scalars['GUID']['output'];
  position: Scalars['Int']['output'];
  run?: Maybe<Scalars['String']['output']>;
  stepId: Scalars['String']['output'];
  uses?: Maybe<Scalars['String']['output']>;
  withParams: Scalars['JSON']['output'];
};

export type AstroliftStepRun = {
  createdAt: Scalars['DateTime']['output'];
  exitCode?: Maybe<Scalars['Int']['output']>;
  finishedAt?: Maybe<Scalars['DateTime']['output']>;
  id: Scalars['GUID']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  step: AstroliftStep;
};

export type AstroliftTaskRun = {
  command: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  durationSeconds?: Maybe<Scalars['Int']['output']>;
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  exitCode?: Maybe<Scalars['Int']['output']>;
  id: Scalars['GUID']['output'];
  k8sJobName: Scalars['String']['output'];
  registeredAppSlug: Scalars['String']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
  triggerKind: Scalars['String']['output'];
  triggeredByUsername?: Maybe<Scalars['String']['output']>;
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftTaskRunPayload = {
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  registeredAppSlug: Scalars['String']['output'];
  status: Scalars['String']['output'];
  workloadSlug: Scalars['String']['output'];
};

export type AstroliftTaskRunPayloadMutationResult = {
  data?: Maybe<AstroliftTaskRunPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
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

export type AstroliftTemplateSendStatPoint = {
  bounces: Scalars['Int']['output'];
  complaints: Scalars['Int']['output'];
  deliveries: Scalars['Int']['output'];
  sends: Scalars['Int']['output'];
  timestamp: Scalars['DateTime']['output'];
};

export type AstroliftTenantCluster = {
  agentProvisioned: Scalars['Boolean']['output'];
  albAuthConfig?: Maybe<Scalars['JSON']['output']>;
  authMethod: Scalars['String']['output'];
  bootstrapRuns: Array<AstroliftClusterBootstrapRun>;
  capabilities: Scalars['JSON']['output'];
  capabilitiesProbedAt?: Maybe<Scalars['DateTime']['output']>;
  createdAt: Scalars['DateTime']['output'];
  endpoint: Scalars['String']['output'];
  heartbeatAgeSeconds?: Maybe<Scalars['Float']['output']>;
  heartbeatIntervalSeconds: Scalars['Int']['output'];
  heartbeatStatus: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  ingressClass: Scalars['String']['output'];
  isActive: Scalars['Boolean']['output'];
  lastBootstrapRun?: Maybe<AstroliftClusterBootstrapRun>;
  lastHeartbeatAt?: Maybe<Scalars['DateTime']['output']>;
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

export type AstroliftToolDef = {
  adapter: Scalars['String']['output'];
  createdAt: Scalars['DateTime']['output'];
  description: Scalars['String']['output'];
  handlerRef: Scalars['String']['output'];
  id: Scalars['GUID']['output'];
  inputSchema: Scalars['JSON']['output'];
  name: Scalars['String']['output'];
  outputSchema: Scalars['JSON']['output'];
  slug: Scalars['String']['output'];
};

export type AstroliftToolDefMutationResult = {
  data?: Maybe<AstroliftToolDef>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftTraceSpan = {
  attributes: Scalars['JSON']['output'];
  durationMs: Scalars['Float']['output'];
  operation: Scalars['String']['output'];
  parentSpanId?: Maybe<Scalars['String']['output']>;
  service: Scalars['String']['output'];
  spanId: Scalars['String']['output'];
  startTime: Scalars['String']['output'];
  statusCode: Scalars['String']['output'];
  traceId: Scalars['String']['output'];
};

export type AstroliftTrigger = {
  config: Scalars['JSON']['output'];
  createdAt: Scalars['DateTime']['output'];
  id: Scalars['GUID']['output'];
  kind: Scalars['String']['output'];
  pipelineId: Scalars['GUID']['output'];
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

export type AstroliftTriggerMutationResult = {
  data?: Maybe<AstroliftTrigger>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftUser = {
  email: Scalars['String']['output'];
  id: Scalars['String']['output'];
  isActive: Scalars['Boolean']['output'];
  username: Scalars['String']['output'];
};

export type AstroliftUserAlertSubscription = {
  alertKind: Scalars['String']['output'];
  appSlug: Scalars['String']['output'];
  channel: Scalars['String']['output'];
  enabled: Scalars['Boolean']['output'];
  id: Scalars['GUID']['output'];
};

export type AstroliftUserAlertSubscriptionMutationResult = {
  data?: Maybe<AstroliftUserAlertSubscription>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AstroliftUserProfile = {
  id: Scalars['String']['output'];
  username?: Maybe<Scalars['String']['output']>;
};

export type AstroliftValidateCiSecretsPayload = {
  repo: Scalars['String']['output'];
  results: Array<AstroliftCiSecretValidation>;
};

export type AstroliftValidateCiSecretsPayloadMutationResult = {
  data?: Maybe<AstroliftValidateCiSecretsPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
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
  version: Scalars['Int']['output'];
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

export type AstroliftWorkflowHistoryEvent = {
  decision: Scalars['String']['output'];
  eventType: Scalars['String']['output'];
  payload: Scalars['JSON']['output'];
  retryCount: Scalars['Int']['output'];
  timestamp: Scalars['String']['output'];
};

export type AstroliftWorkflowInstance = {
  closedAt: Scalars['String']['output'];
  durationSeconds?: Maybe<Scalars['Float']['output']>;
  runId: Scalars['String']['output'];
  startedAt: Scalars['String']['output'];
  status: Scalars['String']['output'];
  taskQueue: Scalars['String']['output'];
  triggeredBy: Scalars['String']['output'];
  workflowId: Scalars['String']['output'];
  workflowType: Scalars['String']['output'];
};

export type AstroliftWorkflowInstanceDetail = {
  history: Array<AstroliftWorkflowHistoryEvent>;
  instance: AstroliftWorkflowInstance;
};

export type AstroliftWorkflowInstancePage = {
  items: Array<AstroliftWorkflowInstance>;
  nextCursor?: Maybe<Scalars['String']['output']>;
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
  volumes: Scalars['JSON']['output'];
};

export type AstroliftWorkloadManifest = {
  appSlug: Scalars['String']['output'];
  environmentName: Scalars['String']['output'];
  error?: Maybe<Scalars['String']['output']>;
  errorColumn?: Maybe<Scalars['Int']['output']>;
  errorLine?: Maybe<Scalars['Int']['output']>;
  errorPath?: Maybe<Scalars['String']['output']>;
  imageTag: Scalars['String']['output'];
  namespace: Scalars['String']['output'];
  previousDeploymentId: Scalars['String']['output'];
  previousImageTag: Scalars['String']['output'];
  resources: Scalars['JSON']['output'];
  resourcesPrevious: Scalars['JSON']['output'];
  workloadSlug: Scalars['String']['output'];
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

export type AstroliftWorkloadResourceGauge = {
  current: Scalars['Float']['output'];
  limit: Scalars['Float']['output'];
  percentOfLimit: Scalars['Float']['output'];
  percentOfRequest: Scalars['Float']['output'];
  request: Scalars['Float']['output'];
  unit: Scalars['String']['output'];
};

export type AstroliftWorkloadResourceUsage = {
  cpu: AstroliftWorkloadResourceGauge;
  memory: AstroliftWorkloadResourceGauge;
  sourcedAt: Scalars['DateTime']['output'];
};

export type AstroliftWorkloadScalingStatus = {
  currentReplicas: Scalars['Int']['output'];
  desiredReplicas: Scalars['Int']['output'];
  hpaEnabled: Scalars['Boolean']['output'];
  hpaMaxReplicas?: Maybe<Scalars['Int']['output']>;
  hpaMinReplicas?: Maybe<Scalars['Int']['output']>;
  hpaTargetCpuPct: Scalars['Int']['output'];
  isScaling: Scalars['Boolean']['output'];
  replicaLowerBound: Scalars['Int']['output'];
  replicaUpperBound: Scalars['Int']['output'];
  sourcedAt: Scalars['DateTime']['output'];
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
  pendingProposalId?: Maybe<Scalars['GUID']['output']>;
};

export type AttachmentremovedpayloadMutationResult = {
  data?: Maybe<Attachmentremovedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type AttestSessionInput = {
  attestationObject: InputMaybe<Scalars['String']['input']>;
  challenge: Scalars['String']['input'];
  integrityToken: InputMaybe<Scalars['String']['input']>;
  keyId: InputMaybe<Scalars['String']['input']>;
  kind: Scalars['String']['input'];
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

export type BulkAppResultItem = {
  appSlug: Scalars['String']['output'];
  errors: Array<Scalars['String']['output']>;
  ok: Scalars['Boolean']['output'];
};

export type BulkApproveDeploymentsInput = {
  deploymentIds: Array<Scalars['GUID']['input']>;
  reason: InputMaybe<Scalars['String']['input']>;
};

export type BulkAssignTeamMemberRolesInput = {
  memberIds: Array<Scalars['GUID']['input']>;
  roleId: Scalars['GUID']['input'];
  teamId: Scalars['GUID']['input'];
};

export type BulkImportAppSecretsInput = {
  appSlug: Scalars['String']['input'];
  dotenvText: Scalars['String']['input'];
};

export type BulkOperationResult = {
  failedCount: Scalars['Int']['output'];
  okCount: Scalars['Int']['output'];
  perApp: Array<BulkAppResultItem>;
};

export type BulkPushSecretsInput = {
  appSlugs: Array<Scalars['String']['input']>;
  bundleSlug: Scalars['String']['input'];
  environmentName: InputMaybe<Scalars['String']['input']>;
};

export type BulkRejectDeploymentsInput = {
  deploymentIds: Array<Scalars['GUID']['input']>;
  reason: Scalars['String']['input'];
};

export type BulkResyncManifestInput = {
  appSlugs: Array<Scalars['String']['input']>;
};

export type BulkRevokeRoleBindingsInput = {
  bindingIds: Array<Scalars['GUID']['input']>;
};

export type BulkRollingRestartInput = {
  appSlugs: Array<Scalars['String']['input']>;
  environmentName: InputMaybe<Scalars['String']['input']>;
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

export type CancelDeregisterInput = {
  reason: InputMaybe<Scalars['String']['input']>;
  workflowId: Scalars['String']['input'];
};

export type ClearAlertSubscriptionInput = {
  id: Scalars['GUID']['input'];
};

export type ClearEnvironmentSettingInput = {
  environmentId: Scalars['GUID']['input'];
  key: Scalars['String']['input'];
};

export type Clusteragentkeyissuedpayload = {
  agentKey: Scalars['String']['output'];
  clusterId: Scalars['GUID']['output'];
  heartbeatUrl: Scalars['String']['output'];
  intervalSeconds: Scalars['Int']['output'];
  rotated: Scalars['Boolean']['output'];
};

export type ClusteragentkeyissuedpayloadMutationResult = {
  data?: Maybe<Clusteragentkeyissuedpayload>;
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
  appClientId: InputMaybe<Scalars['String']['input']>;
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

export type CreateAgentEnvironmentSpecInput = {
  agentType: Scalars['String']['input'];
  allowInstall: Scalars['Boolean']['input'];
  configBranch: Scalars['String']['input'];
  configManifestPath: Scalars['String']['input'];
  configRepo: Scalars['String']['input'];
  envVars: InputMaybe<Scalars['JSON']['input']>;
  imageTag: Scalars['String']['input'];
  name: Scalars['String']['input'];
  runtime: Scalars['String']['input'];
  secretRefs: InputMaybe<Scalars['JSON']['input']>;
  slug: Scalars['String']['input'];
  toolPreset: Scalars['String']['input'];
  vncEnabled: Scalars['Boolean']['input'];
};

export type CreateAlertRuleInput = {
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  managedServiceId: InputMaybe<Scalars['GUID']['input']>;
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

export type CreateEmailTemplateInput = {
  htmlBody: Scalars['String']['input'];
  managedServiceId: Scalars['GUID']['input'];
  name: Scalars['String']['input'];
  subject: Scalars['String']['input'];
  textBody: Scalars['String']['input'];
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

export type CreatePipelineInput = {
  defaultBranch: Scalars['String']['input'];
  name: Scalars['String']['input'];
  repoUrl: Scalars['String']['input'];
  tomlPath: Scalars['String']['input'];
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

export type CreatePreviewEnvironmentInput = {
  appSlug: Scalars['String']['input'];
  branch: Scalars['String']['input'];
  environmentName: Scalars['String']['input'];
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

export type CreateTriggerInput = {
  config: Scalars['String']['input'];
  kind: Scalars['String']['input'];
  pipelineId: Scalars['GUID']['input'];
};

export type CreateWebhookSubscriptionInput = {
  appSlug: InputMaybe<Scalars['String']['input']>;
  events: Array<Scalars['String']['input']>;
  format: InputMaybe<Scalars['String']['input']>;
  teamSlug: InputMaybe<Scalars['String']['input']>;
  url: Scalars['String']['input'];
};

export type CreateWorkflowStageResult = {
  errors: Array<ValidationError>;
  ok: Scalars['Boolean']['output'];
  stage?: Maybe<WorkflowStageType>;
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

export type DeleteEmailTemplateInput = {
  managedServiceId: Scalars['GUID']['input'];
  name: Scalars['String']['input'];
};

export type DeleteFormDefinitionInput = {
  slug: Scalars['String']['input'];
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

export type DeployClusterAgentInput = {
  clusterId: Scalars['GUID']['input'];
};

export type DeployTokenSecretReveal = {
  plaintextSecret: Scalars['String']['output'];
  rotationGraceSeconds: Scalars['Int']['output'];
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

export type DomainPathRouteInput = {
  pathPrefix: Scalars['String']['input'];
  priority: Scalars['Int']['input'];
  stripPrefix: Scalars['Boolean']['input'];
  targetPort: Scalars['Int']['input'];
  targetWorkloadSlug: Scalars['String']['input'];
};

export type DomainRedirectRuleInput = {
  destinationUrl: Scalars['String']['input'];
  httpStatus: Scalars['Int']['input'];
  kind: Scalars['String']['input'];
  preserveQueryString: Scalars['Boolean']['input'];
  priority: Scalars['Int']['input'];
  sourcePattern: Scalars['String']['input'];
};

export type ElevateAdminSessionInput = {
  credential: Scalars['String']['input'];
  method: Scalars['String']['input'];
  ttlSeconds: InputMaybe<Scalars['Int']['input']>;
};

export type Emailsuppressionaddpayload = {
  address: Scalars['String']['output'];
  reason: Scalars['String']['output'];
};

export type EmailsuppressionaddpayloadMutationResult = {
  data?: Maybe<Emailsuppressionaddpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type Emailsuppressionremovepayload = {
  address: Scalars['String']['output'];
  removed: Scalars['Boolean']['output'];
};

export type EmailsuppressionremovepayloadMutationResult = {
  data?: Maybe<Emailsuppressionremovepayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type Emailtemplatedeletedpayload = {
  deleted: Scalars['Boolean']['output'];
  name: Scalars['String']['output'];
};

export type EmailtemplatedeletedpayloadMutationResult = {
  data?: Maybe<Emailtemplatedeletedpayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
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

export type ExportAppLogsInput = {
  appSlug: Scalars['String']['input'];
  container: InputMaybe<Scalars['String']['input']>;
  environmentName: InputMaybe<Scalars['String']['input']>;
  format: Scalars['String']['input'];
  level: InputMaybe<Scalars['String']['input']>;
  podName: InputMaybe<Scalars['String']['input']>;
  regex: InputMaybe<Scalars['String']['input']>;
  since: InputMaybe<Scalars['DateTime']['input']>;
  until: InputMaybe<Scalars['DateTime']['input']>;
  workloadSlug: InputMaybe<Scalars['String']['input']>;
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

/** Runtime feature toggle reported by ``astroliftServerInfo``. */
export type FeatureFlagInfo = {
  description?: Maybe<Scalars['String']['output']>;
  enabled: Scalars['Boolean']['output'];
  key: Scalars['String']['output'];
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

export type FormDefinitionInput = {
  description: InputMaybe<Scalars['String']['input']>;
  fieldConfig: InputMaybe<Scalars['JSON']['input']>;
  formType: InputMaybe<Scalars['String']['input']>;
  isPublic: InputMaybe<Scalars['Boolean']['input']>;
  logicRules: InputMaybe<Scalars['JSON']['input']>;
  name: Scalars['String']['input'];
  schema: Scalars['JSON']['input'];
  scoring: InputMaybe<Scalars['JSON']['input']>;
  slug: Scalars['String']['input'];
};

export type FormDefinitionUpdateInput = {
  description: InputMaybe<Scalars['String']['input']>;
  fieldConfig: InputMaybe<Scalars['JSON']['input']>;
  formType: InputMaybe<Scalars['String']['input']>;
  isPublic: InputMaybe<Scalars['Boolean']['input']>;
  logicRules: InputMaybe<Scalars['JSON']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  schema: InputMaybe<Scalars['JSON']['input']>;
  scoring: InputMaybe<Scalars['JSON']['input']>;
  slug: Scalars['String']['input'];
};

export type GenerateInstallEnrollmentQrInput = {
  label: InputMaybe<Scalars['String']['input']>;
  ttlSeconds: InputMaybe<Scalars['Int']['input']>;
};

export type GenerateSshDeployKeyInput = {
  appSlug: InputMaybe<Scalars['String']['input']>;
  name: Scalars['String']['input'];
};

export type GoldenSignalKind =
  | 'ERRORS'
  | 'LATENCY_P50'
  | 'LATENCY_P90'
  | 'LATENCY_P95'
  | 'LATENCY_P99'
  | 'SATURATION_CPU'
  | 'SATURATION_MEMORY'
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

export type IssueClusterAgentKeyInput = {
  clusterId: Scalars['GUID']['input'];
  intervalSeconds: InputMaybe<Scalars['Int']['input']>;
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

export type MarkOnboardingCompleteInput = {
  skip: Scalars['Boolean']['input'];
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
  addEmailSuppressionEntry: EmailsuppressionaddpayloadMutationResult;
  addOrganizationAllowlistDomain: AstroliftOrganizationAllowlistedDomainMutationResult;
  addWildcardDomain: AstroliftAppDomainMutationResult;
  approveDeployment: AstroliftDeploymentMutationResult;
  approveDeploymentByToken: AstroliftDeploymentMutationResult;
  approveSecretChange: AstroliftSecretChangeProposalMutationResult;
  archiveApp: AstroliftRegisteredAppMutationResult;
  archiveAppRegistryRepo: AstroliftCapabilityDeprovisionPayloadMutationResult;
  archiveForm: AstroliftFormDefinitionMutationResult;
  assembleBrief: AstroliftAssembleBriefResultMutationResult;
  assertSession: AstroliftAttestationResultMutationResult;
  assignAstroliftAppToProject: AstroliftRegisteredAppMutationResult;
  astroliftAnonymizeUser: AstroliftAnonymizeUserPayloadMutationResult;
  astroliftConnectUserSourceProvider: AstroliftConnectUserSourceProviderPayloadMutationResult;
  astroliftDisconnectUserSourceProvider: AstroliftDisconnectUserSourceProviderPayloadMutationResult;
  attachSecretBundle: AstroliftAppSecretBundleAttachmentMutationResult;
  attestSession: AstroliftAttestationResultMutationResult;
  bringClusterIntoManagement: AstroliftTenantClusterMutationResult;
  bulkApproveDeployments: AstroliftBulkDeploymentResultDataMutationResult;
  bulkAssignAstroliftTeamMemberRoles: AstroliftBulkAssignTeamMemberRolesPayloadMutationResult;
  bulkImportAppSecrets: BulkimportpayloadMutationResult;
  bulkPushSecrets: BulkOperationResult;
  bulkRejectDeployments: AstroliftBulkDeploymentResultDataMutationResult;
  bulkResyncManifest: BulkOperationResult;
  bulkRevokeAstroliftRoleBindings: AstroliftBulkRevokeRoleBindingsPayloadMutationResult;
  bulkRollingRestart: BulkOperationResult;
  cancelAstroliftDeregister: AstroliftCancelDeregisterPayloadMutationResult;
  cancelPipelineRun: AstroliftPipelineRunMutationResult;
  cancelTask: NoneTypeMutationResult;
  cancelWorkflowInstance: MutationResult;
  clearAlertSubscription: AstroliftUserAlertSubscriptionMutationResult;
  clearEnvironmentSetting: AstroliftEnvironmentSettingMutationResult;
  configureProviderPlugin: ProviderpluginconfigpayloadMutationResult;
  /** Confirm or update a previously uploaded file. Set delete=true to soft-delete the upload. */
  confirmPreSignedUrlImageUpload: ConfirmUploadResult;
  connectSource: AstroliftSourceConnectionMutationResult;
  createAgentEnvironmentSpec: AstroliftAgentEnvironmentSpecMutationResult;
  createAlertRule: AstroliftAlertRuleMutationResult;
  createApiToken: AstroliftApiTokenPlaintextMutationResult;
  createDeployToken: DeployTokenSecretRevealMutationResult;
  createEmailTemplate: AstroliftEmailTemplateMutationResult;
  createFormDefinition: AstroliftFormDefinitionMutationResult;
  createIdentityProvider: AstroliftIdentityProviderMutationResult;
  createInvitation: AstroliftInvitationCreatedMutationResult;
  createManagedDomain: AstroliftManagedDomainMutationResult;
  createOrganization: AstroliftOrganizationMutationResult;
  createPipeline: AstroliftPipelineMutationResult;
  createPolicy: AstroliftPolicyMutationResult;
  createPreviewEnvironment: AstroliftPreviewEnvironmentMutationResult;
  createProject: AstroliftProjectMutationResult;
  createRole: AstroliftRoleMutationResult;
  createSkill: AstroliftSkillMutationResult;
  createTeam: AstroliftTeamMutationResult;
  createToolDef: AstroliftToolDefMutationResult;
  createTrigger: AstroliftTriggerMutationResult;
  createWebhookSubscription: WebhookSecretRevealMutationResult;
  /** Create a new workflow definition (staff only). */
  createWorkflowDefinition: MutationResult;
  /** Add a stage to an agent workflow definition (staff only). */
  createWorkflowStage: CreateWorkflowStageResult;
  decommissionCluster: AstroliftTenantClusterMutationResult;
  deelevateAdminSession: AstroliftDeelevatePayloadMutationResult;
  /** Delete an object by its global ID (soft-delete via delete_check). */
  delete: Scalars['Boolean']['output'];
  deleteAgentEnvironmentSpec: AstroliftAgentEnvironmentSpecMutationResult;
  deleteAlertRule: AlertruledeletedpayloadMutationResult;
  deleteAppDnsRecord: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppIdentityRole: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppIngress: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppSecret: AppsecretwritepayloadMutationResult;
  deleteDeployment: AstroliftDeploymentMutationResult;
  deleteEmailTemplate: EmailtemplatedeletedpayloadMutationResult;
  deleteFormDefinition: AstroliftFormDefinitionMutationResult;
  deletePipeline: AstroliftPipelineMutationResult;
  deleteSkill: AstroliftSkillMutationResult;
  deleteSshDeployKey: AstroliftSshDeployKeyMutationResult;
  deleteToolDef: AstroliftToolDefMutationResult;
  deleteWebhookSubscription: SoftdeletepayloadMutationResult;
  /** Delete a workflow definition by slug (staff only). */
  deleteWorkflowDefinition: MutationResult;
  deployClusterAgent: AstroliftTenantClusterMutationResult;
  deprovisionManagedService: ManagedservicedeletedpayloadMutationResult;
  deregisterAstroliftApp: AstroliftDeregisterAppPayloadMutationResult;
  detachSecretBundle: AttachmentremovedpayloadMutationResult;
  disconnectSource: AstroliftSourceConnectionMutationResult;
  elevateAdminSession: AstroliftElevatePayloadMutationResult;
  exportAstroliftAppLogs: AstroliftAppLogExportMutationResult;
  exportAuditEvents: AstroliftAuditExportMutationResult;
  extendPreviewTtl: AstroliftPreviewEnvironmentMutationResult;
  /** Upload a file and get a pre-signed URL. Creates a FileUpload wrapper around the Upload. */
  fileUpload: FileUploadResult;
  forceAstroliftRedeploy: AstroliftForceRedeployPayloadMutationResult;
  generateInstallEnrollmentQr: AstroliftEnrollmentQrPayloadMutationResult;
  /** Generate a temporary authentication token for Rocket.Chat. TTL is configured on the Rocket.Chat server. */
  generateRocketChatToken: Scalars['String']['output'];
  generateSshDeployKey: AstroliftSshDeployKeyCreatedMutationResult;
  grantRole: AstroliftRoleBindingMutationResult;
  grantTeamAccessToApp: AstroliftAppTeamAccessMutationResult;
  heartbeatSession: AstroliftHeartbeatSessionPayloadMutationResult;
  importSkillsFromRepo: AstroliftImportSkillsResultMutationResult;
  installAstroliftSourceWebhook: AstroliftInstallSourceWebhookPayloadMutationResult;
  installClusterPrereqs: AstroliftTenantClusterMutationResult;
  installScmWebhook: AstroliftScmWebhookInstallationMutationResult;
  issueClusterAgentKey: ClusteragentkeyissuedpayloadMutationResult;
  launchTask: AstroliftLaunchTaskResultMutationResult;
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
  markOnboardingComplete: AstroliftMarkOnboardingCompletePayloadMutationResult;
  migrateAppToCluster: AstroliftAppEnvironmentMutationResult;
  moveAppToTeam: AstroliftRegisteredAppMutationResult;
  muteAlertRule: AstroliftAlertRuleMutationResult;
  /** Create or update a notification via NotificationSerializer. */
  notification: MutationResult;
  /** Mark a notification as read. */
  notificationRead: Scalars['Boolean']['output'];
  organization: OrganizationMutationResult;
  organizationMemberStatus: MutationResult;
  /** Force a workflow instance to a specific state (admin override). */
  overrideWorkflowState: MutationResult;
  pauseAppIngress: AstroliftAppEnvironmentMutationResult;
  pauseAstroliftAppWebhookDeploys: AstroliftRegisteredAppMutationResult;
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
  proposeSecretChange: AstroliftSecretChangeProposalMutationResult;
  provisionManagedDomain: ProvisionManagedDomainPayloadMutationResult;
  provisionManagedService: AstroliftManagedServiceMutationResult;
  publishForm: AstroliftFormDefinitionMutationResult;
  pushAstroliftCiSecretsToRepo: AstroliftPushCiSecretsPayloadMutationResult;
  pushAstroliftCiWorkflowToRepo: AstroliftPushCiWorkflowPayloadMutationResult;
  pushCiWorkflow: AstroliftScmPushCiWorkflowResultMutationResult;
  pushManifestToRepo: ManifestpushpayloadMutationResult;
  recheckDomainValidation: AstroliftAppDomainMutationResult;
  reconcileClusterIngresses: ReconcileClusterIngressesResultMutationResult;
  recordClusterBootstrapRun: BootstraprunrecordedpayloadMutationResult;
  redeployApp: AstroliftDeploymentMutationResult;
  refreshClusterManagement: AstroliftTenantClusterMutationResult;
  registerAgentRepo: AstroliftRegisterAgentRepoResultMutationResult;
  registerApp: AstroliftRegisteredAppMutationResult;
  registerMobileDevice: AstroliftDeviceRegistrationMutationResult;
  registerTenantCluster: AstroliftTenantClusterMutationResult;
  reissueManagedDomainCert: ReissueManagedDomainCertPayloadMutationResult;
  rejectDeployment: AstroliftDeploymentMutationResult;
  rejectDeploymentByToken: AstroliftDeploymentMutationResult;
  rejectSecretChange: AstroliftSecretChangeProposalMutationResult;
  removeAppDomain: AppdomainremovedpayloadMutationResult;
  removeEmailSuppressionEntry: EmailsuppressionremovepayloadMutationResult;
  removeOrganizationAllowlistDomain: SoftdeletepayloadMutationResult;
  reprovisionManagedService: AstroliftManagedServiceMutationResult;
  requestAttestationChallenge: AstroliftAttestationChallengePayloadMutationResult;
  requestQuotaIncrease: AstroliftQuotaIncreaseRequestMutationResult;
  restartAstroliftWorkload: AstroliftWorkloadOpPayloadMutationResult;
  restoreApp: AstroliftRegisteredAppMutationResult;
  resumeAppIngress: AstroliftAppEnvironmentMutationResult;
  resumeAstroliftAppWebhookDeploys: AstroliftRegisteredAppMutationResult;
  resumeEnvironment: AstroliftAppEnvironmentMutationResult;
  resyncAstroliftManifestFromRepo: ResyncManifestPayloadMutationResult;
  revalidateManagedDomain: RevalidateManagedDomainPayloadMutationResult;
  revealAppSecret: AstroliftRevealedSecretMutationResult;
  revealManagedServiceConnection: AstroliftManagedServiceConnectionMutationResult;
  revokeApiToken: SoftdeletepayloadMutationResult;
  revokeAppCertificate: AstroliftCapabilityDeprovisionPayloadMutationResult;
  revokeAstroliftSession: AstroliftRevokeAstroliftSessionPayloadMutationResult;
  revokeDeployToken: DeploytokenrevokedpayloadMutationResult;
  revokeInvitation: AstroliftInvitationMutationResult;
  revokeMobileDevice: RevokemobiledevicepayloadMutationResult;
  revokeRoleBinding: SoftdeletepayloadMutationResult;
  revokeTeamAccessFromApp: SoftdeletepayloadMutationResult;
  rollbackDeployment: AstroliftDeploymentMutationResult;
  rotateAppSecret: AppsecretwritepayloadMutationResult;
  rotateDeployToken: DeployTokenSecretRevealMutationResult;
  rotateOutboundWebhookSecret: WebhookSecretRevealMutationResult;
  rotateSecretBundle: AstroliftSecretBundleMutationResult;
  rotateWebhookSecret: AstroliftScmWebhookSecretRevealMutationResult;
  runAstroliftAgent: AstroliftAgentTaskMutationResult;
  runAstroliftJobOnce: AstroliftRunJobOncePayloadMutationResult;
  runTask: AstroliftTaskRunPayloadMutationResult;
  /** Run an agent WorkflowDefinition's stages durably via Temporal (WorkflowDefinitionRunWorkflow). Creates the WorkflowInstance + WorkflowRun mirror rows and enqueues the stage executor. */
  runWorkflowDefinition: RunWorkflowDefinitionResult;
  scaleAstroliftWorkload: AstroliftWorkloadOpPayloadMutationResult;
  sendManagedServiceTestEmail: AstroliftManagedServiceTestEmailResultMutationResult;
  setActiveIdentityProvider: AstroliftIdentityProviderMutationResult;
  setAlertSubscription: AstroliftUserAlertSubscriptionMutationResult;
  setAppSecret: AppsecretwritepayloadMutationResult;
  setAppSecretMetadata: AppsecretmetadatapayloadMutationResult;
  setAppSubdomain: AstroliftRegisteredAppMutationResult;
  setDomainPathRoutes: AstroliftAppDomainMutationResult;
  setDomainRedirects: AstroliftAppDomainMutationResult;
  setEnvironmentSetting: AstroliftEnvironmentSettingMutationResult;
  setNotificationPreference: AstroliftNotificationPreferenceMutationResult;
  setRetentionPolicy: AstroliftRetentionPolicyMutationResult;
  /** Cancel a sign request. Requires SIGNREQUEST_CHANGE_CANCEL permission. */
  signRequestCancel: Scalars['Boolean']['output'];
  /** Sign a sign request. Requires SIGNREQUEST_CHANGE_SIGN permission and an active PIN transaction. Status must be SIGN_REQUIRED. */
  signRequestSign: Scalars['Boolean']['output'];
  /** Request a sign from a user. The user must have SIGNREQUEST_CHANGE_SIGN permission. */
  signRequestUser: Scalars['Boolean']['output'];
  signalWorkflowInstance: MutationResult;
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
  submitForm: AstroliftFormSubmissionMutationResult;
  /** Switch the active user (impersonation). */
  switchUser: SwitchUserResult;
  syncManifestFromRepo: ManifeststagepayloadMutationResult;
  tearDownApp: SoftdeletepayloadMutationResult;
  tearDownPreview: AstroliftDeploymentMutationResult;
  terminateWorkflowInstance: MutationResult;
  testNotificationChannel: AstroliftNotificationMutationResult;
  testWebhookSubscription: AstroliftWebhookTestResultMutationResult;
  transferApp: AstroliftRegisteredAppMutationResult;
  /** Transition a workflow instance to a new state. */
  transitionWorkflow: MutationResult;
  triggerAstroliftDeployWorkflow: AstroliftTriggerDeployWorkflowPayloadMutationResult;
  triggerPipelineRun: AstroliftPipelineRunMutationResult;
  unmuteAlertRule: AstroliftAlertRuleMutationResult;
  unregisterTenantCluster: SoftdeletepayloadMutationResult;
  updateAgentEnvironmentSpec: AstroliftAgentEnvironmentSpecMutationResult;
  updateAgentRunSpec: AstroliftAgentRunSpecMutationResult;
  updateAlertRule: AstroliftAlertRuleMutationResult;
  updateApp: AstroliftRegisteredAppMutationResult;
  updateAstroliftSecurityPolicy: AstroliftRegisteredAppMutationResult;
  updateEmailTemplate: AstroliftEmailTemplateMutationResult;
  updateFormDefinition: AstroliftFormDefinitionMutationResult;
  updateIdentityProvider: AstroliftIdentityProviderMutationResult;
  updateManagedDomain: AstroliftManagedDomainMutationResult;
  updateManagedService: AstroliftManagedServiceMutationResult;
  updateManifest: ManifeststagepayloadMutationResult;
  updateMyProfile: AstroliftMyProfileMutationResult;
  updateOrganization: AstroliftOrganizationMutationResult;
  updatePipeline: AstroliftPipelineMutationResult;
  updatePolicy: AstroliftPolicyMutationResult;
  updateProject: AstroliftProjectMutationResult;
  updateRole: AstroliftRoleMutationResult;
  updateSkill: AstroliftSkillMutationResult;
  updateSourceConnection: AstroliftSourceConnectionMutationResult;
  updateSubmissionStatus: AstroliftFormSubmissionMutationResult;
  updateTeam: AstroliftTeamMutationResult;
  updateTenantCluster: AstroliftTenantClusterMutationResult;
  updateToolDef: AstroliftToolDefMutationResult;
  updateWebhookSubscription: AstroliftWebhookSubscriptionMutationResult;
  /** Update an existing workflow definition (staff only). */
  updateWorkflowDefinition: MutationResult;
  uploadCustomDomainCertificate: AstroliftAppDomainMutationResult;
  /** Upload a data import file and get a pre-signed URL. */
  uploadTextFile: DataImportUploadResult;
  upsertOrganization: OrganizationMutationResult;
  /** Upsert user profile via UtilityForm.apply_forms. */
  upsertUser: UpsertUserResult;
  validateAstroliftCiSecrets: AstroliftValidateCiSecretsPayloadMutationResult;
  withdrawSecretChange: AstroliftSecretChangeProposalMutationResult;
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


export type MutationAddEmailSuppressionEntryArgs = {
  input: AddEmailSuppressionEntryInput;
};


export type MutationAddOrganizationAllowlistDomainArgs = {
  input: AddOrganizationAllowlistDomainInput;
};


export type MutationAddWildcardDomainArgs = {
  input: AddWildcardDomainInput;
};


export type MutationApproveDeploymentArgs = {
  input: DeploymentByIdInput;
};


export type MutationApproveDeploymentByTokenArgs = {
  input: ApproveByTokenInput;
};


export type MutationApproveSecretChangeArgs = {
  input: ApproveSecretChangeInput;
};


export type MutationArchiveAppArgs = {
  input: ArchiveAppInput;
};


export type MutationArchiveAppRegistryRepoArgs = {
  input: ArchiveAppRegistryRepoInput;
};


export type MutationArchiveFormArgs = {
  slug: Scalars['String']['input'];
};


export type MutationAssembleBriefArgs = {
  config?: InputMaybe<Scalars['JSON']['input']>;
  orgId: Scalars['ID']['input'];
  skillIds: Array<Scalars['ID']['input']>;
};


export type MutationAssertSessionArgs = {
  input: AssertSessionInput;
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


export type MutationAttestSessionArgs = {
  input: AttestSessionInput;
};


export type MutationBringClusterIntoManagementArgs = {
  input: BringClusterIntoManagementInputType;
};


export type MutationBulkApproveDeploymentsArgs = {
  input: BulkApproveDeploymentsInput;
};


export type MutationBulkAssignAstroliftTeamMemberRolesArgs = {
  input: BulkAssignTeamMemberRolesInput;
};


export type MutationBulkImportAppSecretsArgs = {
  input: BulkImportAppSecretsInput;
};


export type MutationBulkPushSecretsArgs = {
  input: BulkPushSecretsInput;
};


export type MutationBulkRejectDeploymentsArgs = {
  input: BulkRejectDeploymentsInput;
};


export type MutationBulkResyncManifestArgs = {
  input: BulkResyncManifestInput;
};


export type MutationBulkRevokeAstroliftRoleBindingsArgs = {
  input: BulkRevokeRoleBindingsInput;
};


export type MutationBulkRollingRestartArgs = {
  input: BulkRollingRestartInput;
};


export type MutationCancelAstroliftDeregisterArgs = {
  input: CancelDeregisterInput;
};


export type MutationCancelPipelineRunArgs = {
  runId: Scalars['GUID']['input'];
};


export type MutationCancelTaskArgs = {
  id: Scalars['ID']['input'];
};


export type MutationCancelWorkflowInstanceArgs = {
  workflowId: Scalars['String']['input'];
};


export type MutationClearAlertSubscriptionArgs = {
  input: ClearAlertSubscriptionInput;
};


export type MutationClearEnvironmentSettingArgs = {
  input: ClearEnvironmentSettingInput;
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


export type MutationCreateAgentEnvironmentSpecArgs = {
  input: CreateAgentEnvironmentSpecInput;
  orgId: Scalars['ID']['input'];
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


export type MutationCreateEmailTemplateArgs = {
  input: CreateEmailTemplateInput;
};


export type MutationCreateFormDefinitionArgs = {
  input: FormDefinitionInput;
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


export type MutationCreatePipelineArgs = {
  input: CreatePipelineInput;
};


export type MutationCreatePolicyArgs = {
  input: CreatePolicyInput;
};


export type MutationCreatePreviewEnvironmentArgs = {
  input: CreatePreviewEnvironmentInput;
};


export type MutationCreateProjectArgs = {
  input: CreateProjectInput;
};


export type MutationCreateRoleArgs = {
  input: CreateRoleInput;
};


export type MutationCreateSkillArgs = {
  input: SkillInput;
  orgId: Scalars['ID']['input'];
};


export type MutationCreateTeamArgs = {
  input: CreateTeamInput;
};


export type MutationCreateToolDefArgs = {
  input: ToolDefInput;
  skillId: Scalars['ID']['input'];
};


export type MutationCreateTriggerArgs = {
  input: CreateTriggerInput;
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


export type MutationCreateWorkflowStageArgs = {
  agentDefinitionGuid?: InputMaybe<Scalars['String']['input']>;
  fanOutCount?: InputMaybe<Scalars['Int']['input']>;
  kind: Scalars['String']['input'];
  onFailure?: Scalars['String']['input'];
  order: Scalars['Int']['input'];
  skillRefs?: InputMaybe<Scalars['JSON']['input']>;
  timeoutSeconds?: Scalars['Int']['input'];
  workflowSlug: Scalars['String']['input'];
};


export type MutationDecommissionClusterArgs = {
  input: DecommissionClusterInputType;
};


export type MutationDeleteArgs = {
  gid: Scalars['ID']['input'];
};


export type MutationDeleteAgentEnvironmentSpecArgs = {
  slug: Scalars['String']['input'];
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


export type MutationDeleteDeploymentArgs = {
  input: DeploymentByIdInput;
};


export type MutationDeleteEmailTemplateArgs = {
  input: DeleteEmailTemplateInput;
};


export type MutationDeleteFormDefinitionArgs = {
  input: DeleteFormDefinitionInput;
};


export type MutationDeletePipelineArgs = {
  id: Scalars['GUID']['input'];
};


export type MutationDeleteSkillArgs = {
  id: Scalars['ID']['input'];
};


export type MutationDeleteSshDeployKeyArgs = {
  input: DeleteSshDeployKeyInput;
};


export type MutationDeleteToolDefArgs = {
  id: Scalars['ID']['input'];
};


export type MutationDeleteWebhookSubscriptionArgs = {
  input: DeleteWebhookSubscriptionInput;
};


export type MutationDeleteWorkflowDefinitionArgs = {
  slug: Scalars['String']['input'];
};


export type MutationDeployClusterAgentArgs = {
  input: DeployClusterAgentInput;
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


export type MutationElevateAdminSessionArgs = {
  input: ElevateAdminSessionInput;
};


export type MutationExportAstroliftAppLogsArgs = {
  input: ExportAppLogsInput;
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


export type MutationGenerateInstallEnrollmentQrArgs = {
  input: GenerateInstallEnrollmentQrInput;
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


export type MutationImportSkillsFromRepoArgs = {
  branch?: Scalars['String']['input'];
  manifestPath?: Scalars['String']['input'];
  repoUrl: Scalars['String']['input'];
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


export type MutationIssueClusterAgentKeyArgs = {
  input: IssueClusterAgentKeyInput;
};


export type MutationLaunchTaskArgs = {
  briefId: Scalars['ID']['input'];
  callbackUrl?: InputMaybe<Scalars['String']['input']>;
  orgId: Scalars['ID']['input'];
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


export type MutationMarkOnboardingCompleteArgs = {
  input: MarkOnboardingCompleteInput;
};


export type MutationMigrateAppToClusterArgs = {
  input: MigrateAppInputGql;
};


export type MutationMoveAppToTeamArgs = {
  input: MoveAppToTeamInput;
};


export type MutationMuteAlertRuleArgs = {
  input: MuteAlertRuleInput;
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


export type MutationPauseAstroliftAppWebhookDeploysArgs = {
  input: PauseAppWebhookDeploysInput;
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


export type MutationProposeSecretChangeArgs = {
  input: ProposeSecretChangeInput;
};


export type MutationProvisionManagedDomainArgs = {
  clusterId: Scalars['GUID']['input'];
  isPlatformManagedZone?: Scalars['Boolean']['input'];
  zone: Scalars['String']['input'];
};


export type MutationProvisionManagedServiceArgs = {
  input: ProvisionManagedServiceInput;
};


export type MutationPublishFormArgs = {
  slug: Scalars['String']['input'];
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


export type MutationReconcileClusterIngressesArgs = {
  input: ReconcileClusterIngressesInput;
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


export type MutationRegisterAgentRepoArgs = {
  input: RegisterAgentRepoInput;
};


export type MutationRegisterAppArgs = {
  input: RegisterAppInput;
};


export type MutationRegisterMobileDeviceArgs = {
  input: RegisterMobileDeviceInput;
};


export type MutationRegisterTenantClusterArgs = {
  input: RegisterTenantClusterInput;
};


export type MutationReissueManagedDomainCertArgs = {
  clusterId: Scalars['GUID']['input'];
  zone: Scalars['String']['input'];
};


export type MutationRejectDeploymentArgs = {
  input: AbortDeploymentInput;
};


export type MutationRejectDeploymentByTokenArgs = {
  input: RejectByTokenInput;
};


export type MutationRejectSecretChangeArgs = {
  input: RejectSecretChangeInput;
};


export type MutationRemoveAppDomainArgs = {
  input: RemoveAppDomainInput;
};


export type MutationRemoveEmailSuppressionEntryArgs = {
  input: RemoveEmailSuppressionEntryInput;
};


export type MutationRemoveOrganizationAllowlistDomainArgs = {
  input: RemoveOrganizationAllowlistDomainInput;
};


export type MutationReprovisionManagedServiceArgs = {
  input: ReprovisionManagedServiceInput;
};


export type MutationRequestAttestationChallengeArgs = {
  input: RequestAttestationChallengeInput;
};


export type MutationRequestQuotaIncreaseArgs = {
  input: RequestQuotaIncreaseInput;
};


export type MutationRestartAstroliftWorkloadArgs = {
  input: RestartWorkloadInput;
};


export type MutationRestoreAppArgs = {
  input: RestoreAppInput;
};


export type MutationResumeAppIngressArgs = {
  input: EnvironmentByIdInput;
};


export type MutationResumeAstroliftAppWebhookDeploysArgs = {
  input: ResumeAppWebhookDeploysInput;
};


export type MutationResumeEnvironmentArgs = {
  input: EnvironmentByIdInput;
};


export type MutationResyncAstroliftManifestFromRepoArgs = {
  input: ResyncManifestFromRepoInput;
};


export type MutationRevalidateManagedDomainArgs = {
  clusterId: Scalars['GUID']['input'];
  zone: Scalars['String']['input'];
};


export type MutationRevealAppSecretArgs = {
  input: RevealAppSecretInput;
};


export type MutationRevealManagedServiceConnectionArgs = {
  input: RevealManagedServiceConnectionInput;
};


export type MutationRevokeApiTokenArgs = {
  input: RevokeApiTokenInput;
};


export type MutationRevokeAppCertificateArgs = {
  input: RevokeAppCertificateInput;
};


export type MutationRevokeAstroliftSessionArgs = {
  input: RevokeAstroliftSessionInput;
};


export type MutationRevokeDeployTokenArgs = {
  input: RevokeDeployTokenInput;
};


export type MutationRevokeInvitationArgs = {
  input: RevokeInvitationInput;
};


export type MutationRevokeMobileDeviceArgs = {
  input: RevokeMobileDeviceInput;
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


export type MutationRotateAppSecretArgs = {
  input: RotateAppSecretInput;
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


export type MutationRunAstroliftAgentArgs = {
  input: RunAstroliftAgentInput;
};


export type MutationRunAstroliftJobOnceArgs = {
  input: RunJobOnceInput;
};


export type MutationRunTaskArgs = {
  input: RunTaskInput;
};


export type MutationRunWorkflowDefinitionArgs = {
  triggerPayload?: InputMaybe<Scalars['JSON']['input']>;
  workflowSlug: Scalars['String']['input'];
};


export type MutationScaleAstroliftWorkloadArgs = {
  input: ScaleWorkloadInput;
};


export type MutationSendManagedServiceTestEmailArgs = {
  input: SendManagedServiceTestEmailInput;
};


export type MutationSetActiveIdentityProviderArgs = {
  input: SetActiveIdentityProviderInput;
};


export type MutationSetAlertSubscriptionArgs = {
  input: SetAlertSubscriptionInput;
};


export type MutationSetAppSecretArgs = {
  input: SetAppSecretInput;
};


export type MutationSetAppSecretMetadataArgs = {
  input: SetAppSecretMetadataInput;
};


export type MutationSetAppSubdomainArgs = {
  input: SetAppSubdomainInput;
};


export type MutationSetDomainPathRoutesArgs = {
  input: SetDomainPathRoutesInput;
};


export type MutationSetDomainRedirectsArgs = {
  input: SetDomainRedirectsInput;
};


export type MutationSetEnvironmentSettingArgs = {
  input: SetEnvironmentSettingInput;
};


export type MutationSetNotificationPreferenceArgs = {
  input: SetNotificationPreferenceInput;
};


export type MutationSetRetentionPolicyArgs = {
  input: SetRetentionPolicyInput;
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


export type MutationSignalWorkflowInstanceArgs = {
  payload?: InputMaybe<Scalars['JSON']['input']>;
  signalName: Scalars['String']['input'];
  workflowId: Scalars['String']['input'];
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


export type MutationSubmitFormArgs = {
  payload: Scalars['JSON']['input'];
  slug: Scalars['String']['input'];
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


export type MutationTerminateWorkflowInstanceArgs = {
  reason: Scalars['String']['input'];
  workflowId: Scalars['String']['input'];
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


export type MutationTriggerPipelineRunArgs = {
  pipelineId: Scalars['GUID']['input'];
  ref?: InputMaybe<Scalars['String']['input']>;
};


export type MutationUnmuteAlertRuleArgs = {
  input: UnmuteAlertRuleInput;
};


export type MutationUnregisterTenantClusterArgs = {
  input: UnregisterTenantClusterInput;
};


export type MutationUpdateAgentEnvironmentSpecArgs = {
  input: UpdateAgentEnvironmentSpecInput;
  slug: Scalars['String']['input'];
};


export type MutationUpdateAgentRunSpecArgs = {
  agentSlug: Scalars['String']['input'];
  input: AgentRunSpecInput;
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


export type MutationUpdateEmailTemplateArgs = {
  input: UpdateEmailTemplateInput;
};


export type MutationUpdateFormDefinitionArgs = {
  input: FormDefinitionUpdateInput;
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


export type MutationUpdatePipelineArgs = {
  id: Scalars['GUID']['input'];
  input: UpdatePipelineInput;
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


export type MutationUpdateSkillArgs = {
  id: Scalars['ID']['input'];
  input: SkillInput;
};


export type MutationUpdateSourceConnectionArgs = {
  input: UpdateSourceConnectionInput;
};


export type MutationUpdateSubmissionStatusArgs = {
  status: Scalars['String']['input'];
  submissionId: Scalars['GUID']['input'];
};


export type MutationUpdateTeamArgs = {
  input: UpdateTeamInput;
};


export type MutationUpdateTenantClusterArgs = {
  input: UpdateTenantClusterInput;
};


export type MutationUpdateToolDefArgs = {
  id: Scalars['ID']['input'];
  input: ToolDefInput;
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


export type MutationValidateAstroliftCiSecretsArgs = {
  input: ValidateAstroliftCiSecretsInput;
};


export type MutationWithdrawSecretChangeArgs = {
  input: WithdrawSecretChangeInput;
};

export type MutationError = {
  code: Scalars['String']['output'];
  currentVersion?: Maybe<Scalars['Int']['output']>;
  field?: Maybe<Scalars['String']['output']>;
  message: Scalars['String']['output'];
  requestedVersion?: Maybe<Scalars['Int']['output']>;
  requiresAttestation?: Maybe<Scalars['Boolean']['output']>;
  supportedMethods?: Maybe<Array<Scalars['String']['output']>>;
};

/** Standard mutation result with ok flag and validation errors. */
export type MutationResult = {
  errors: Array<ValidationError>;
  ok: Scalars['Boolean']['output'];
};

export type MuteAlertRuleInput = {
  durationSeconds: Scalars['Int']['input'];
  reason: Scalars['String']['input'];
  ruleId: Scalars['GUID']['input'];
};

export type NoneTypeMutationResult = {
  data?: Maybe<Scalars['Void']['output']>;
  errors: Array<MutationError>;
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

export type PauseAppWebhookDeploysInput = {
  appSlug: Scalars['String']['input'];
  reason: InputMaybe<Scalars['String']['input']>;
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

export type ProposeSecretChangeInput = {
  appSlug: Scalars['String']['input'];
  attachmentId: InputMaybe<Scalars['GUID']['input']>;
  bundleSlug: InputMaybe<Scalars['String']['input']>;
  environmentName: InputMaybe<Scalars['String']['input']>;
  key: InputMaybe<Scalars['String']['input']>;
  op: Scalars['String']['input'];
  prefix: InputMaybe<Scalars['String']['input']>;
  value: InputMaybe<Scalars['String']['input']>;
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

export type ProvisionManagedDomainPayload = {
  message: Scalars['String']['output'];
  nameservers: Array<Scalars['String']['output']>;
  workflowId: Scalars['String']['output'];
  zone: Scalars['String']['output'];
};

export type ProvisionManagedDomainPayloadMutationResult = {
  data?: Maybe<ProvisionManagedDomainPayload>;
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
  ObjectStoreMetrics: Array<Array<Scalars['String']['output']>>;
  PostgresMetrics: Array<Array<Scalars['String']['output']>>;
  agent?: Maybe<AstroliftAgentDetail>;
  agentEnvironmentSpec?: Maybe<AstroliftAgentEnvironmentSpec>;
  agentEnvironmentSpecs: Array<AstroliftAgentEnvironmentSpec>;
  agentFleet: Array<AstroliftAgentListItem>;
  agentGallery: Array<AstroliftAgentTask>;
  agentLiveStatus: Array<AstroliftAgentLiveStatus>;
  agentRuntimes: Array<AstroliftAgentRuntime>;
  agentTask?: Maybe<AstroliftAgentTask>;
  agentTaskLogs: Array<Scalars['String']['output']>;
  agentTasks: Array<AstroliftAgentTask>;
  agentWorkloads: Array<AstroliftAgentListItem>;
  assignableAstroliftProjects: Array<AstroliftProject>;
  astroliftActiveIdentityProvider?: Maybe<AstroliftIdentityProvider>;
  astroliftActiveSessions: Array<AstroliftActiveSession>;
  astroliftAgentRuns: Array<AstroliftAgentRun>;
  astroliftAlertEvents: Array<AstroliftAlertEvent>;
  astroliftAlertRules: Array<AstroliftAlertRule>;
  astroliftApiTokens: Array<AstroliftApiToken>;
  astroliftApp?: Maybe<AstroliftRegisteredApp>;
  astroliftAppCertificates: Array<AstroliftAppCertificate>;
  astroliftAppCountForCluster: Scalars['Int']['output'];
  astroliftAppDeployTokens: Array<AstroliftDeployToken>;
  astroliftAppDnsRecords: Array<AstroliftAppDnsRecord>;
  astroliftAppDomains: Array<AstroliftAppDomain>;
  astroliftAppEndpointMetrics: Array<AstroliftAppEndpointMetric>;
  astroliftAppGoldenSignals: Array<AstroliftAppGoldenSignal>;
  astroliftAppHealthSummary: Array<AstroliftAppHealthSummary>;
  astroliftAppIdentityBinding?: Maybe<AstroliftAppIdentityBinding>;
  astroliftAppLogs: AstroliftAppLogPage;
  astroliftAppManagedServiceMetrics?: Maybe<AstroliftManagedServiceMetrics>;
  astroliftAppMetrics?: Maybe<AstroliftAppMetrics>;
  astroliftAppPods: Array<AstroliftAppPod>;
  astroliftAppSecretBundleAttachments: Array<AstroliftAppSecretBundleAttachment>;
  astroliftAppSecretHistory: Array<AstroliftSecretHistoryEntry>;
  astroliftAppSecrets: Array<AstroliftAppSecret>;
  astroliftAppStatusCodeBreakdown?: Maybe<AstroliftStatusCodeBreakdown>;
  astroliftAppTeamAccesses: Array<AstroliftAppTeamAccess>;
  astroliftAppTraces: Array<AstroliftAppTrace>;
  astroliftAppUrlHealth?: Maybe<AstroliftAppUrlHealth>;
  astroliftAppUrlProbeHistory: Array<AstroliftAppUrlHealth>;
  astroliftApps: Array<AstroliftRegisteredApp>;
  astroliftAppsPage: AstroliftRegisteredAppPage;
  astroliftAuditEvents: Array<AstroliftAuditEvent>;
  astroliftAuditEventsPage: AstroliftAuditEventPage;
  astroliftAuditRetention: AstroliftAuditRetention;
  astroliftAvailableRepos: AstroliftRemoteRepoList;
  astroliftBudgets: Array<AstroliftBudget>;
  astroliftClusterBootstrapPlan?: Maybe<AstroliftClusterBootstrapPlan>;
  astroliftClusterCertificates: AstroliftClusterCertificates;
  astroliftClusterCount: Scalars['Int']['output'];
  astroliftClusterHealth?: Maybe<AstroliftClusterHealth>;
  astroliftClusterLifecycleAudit: Array<AstroliftClusterLifecycleAuditEntry>;
  astroliftClusterLiveState?: Maybe<AstroliftClusterLiveState>;
  astroliftClusterPrometheusMetrics: AstroliftClusterPrometheusMetrics;
  astroliftClusterPrometheusRangeMetrics: AstroliftClusterPrometheusRangeMetrics;
  astroliftClusterWorkloadHealth: Array<AstroliftClusterWorkloadHealth>;
  astroliftClusters: Array<AstroliftTenantCluster>;
  astroliftCognitoUserPoolClients: Array<AstroliftCognitoUserPoolClient>;
  astroliftCognitoUserPools: Array<AstroliftCognitoUserPool>;
  astroliftCommandRuns: Array<AstroliftCommandRun>;
  astroliftCompareDeployments?: Maybe<AstroliftDeploymentComparison>;
  astroliftContainers: Array<AstroliftContainer>;
  astroliftCostByBinding: AstroliftCostAttribution;
  astroliftCostForecast: AstroliftCostForecast;
  astroliftCostSnapshots: Array<AstroliftCostSnapshot>;
  astroliftCostTrend: Array<AstroliftCostTrendPoint>;
  astroliftDeployment?: Maybe<AstroliftDeployment>;
  astroliftDeploymentApprovalHistory: Array<AstroliftDeploymentApprovalHistoryEntry>;
  astroliftDeploymentLog: Array<AstroliftDeploymentLogEntry>;
  astroliftDeploymentMetrics: AstroliftDeploymentMetrics;
  astroliftDeploymentReleaseNotes?: Maybe<AstroliftReleaseNotes>;
  astroliftDeployments: Array<AstroliftDeployment>;
  astroliftDnsCertificates: AstroliftClusterCertificates;
  astroliftDnsZones: AstroliftDnsZones;
  astroliftElevationStatus: AstroliftElevationStatus;
  astroliftEmailEngagementMetrics?: Maybe<AstroliftEmailEngagementMetrics>;
  astroliftEmailMessages: Array<AstroliftEmailMessage>;
  astroliftEmailServiceDetail?: Maybe<AstroliftEmailServiceDetail>;
  astroliftEmailTemplate?: Maybe<AstroliftEmailTemplate>;
  astroliftEmailTemplateStats: Array<AstroliftTemplateSendStatPoint>;
  astroliftEmailTemplates: Array<AstroliftEmailTemplate>;
  astroliftEnvironments: Array<AstroliftAppEnvironment>;
  astroliftEvents: Array<AstroliftEvent>;
  astroliftEventsAggregated: Array<AstroliftAggregatedEvent>;
  astroliftEventsPage: AstroliftEventPage;
  astroliftExecutePromql: AstroliftExecutePromqlResult;
  astroliftIdentityProviders: Array<AstroliftIdentityProvider>;
  astroliftInvitations: Array<AstroliftInvitation>;
  astroliftManagedDomains: Array<AstroliftManagedDomain>;
  astroliftManagedServiceObjects?: Maybe<AstroliftManagedServiceObjects>;
  astroliftManagedServiceQueueDepth?: Maybe<AstroliftManagedServiceQueueDepth>;
  astroliftManagedServices: Array<AstroliftManagedService>;
  astroliftMembers: Array<AstroliftMember>;
  astroliftMyAlertSubscriptions: Array<AstroliftUserAlertSubscription>;
  astroliftMyApps: Array<AstroliftRegisteredApp>;
  astroliftMyAppsPage: AstroliftRegisteredAppPage;
  astroliftMyConnectedAccounts: Array<AstroliftMyConnectedAccount>;
  astroliftMyDevices: Array<AstroliftDeviceRegistration>;
  astroliftMyMobileDevices: Array<AstroliftDeviceRegistration>;
  astroliftMyNotificationPreferences: Array<AstroliftNotificationPreference>;
  astroliftMyNotifications: Array<AstroliftNotification>;
  astroliftMyPermissions: Array<Scalars['String']['output']>;
  astroliftMyProfile?: Maybe<AstroliftMyProfile>;
  astroliftNavTree?: Maybe<AstroliftNavTree>;
  astroliftOrgMembersForApprovalPicker: Array<AstroliftApproverUser>;
  astroliftOrganization?: Maybe<AstroliftOrganization>;
  astroliftOrganizationAllowlistDomains: Array<AstroliftOrganizationAllowlistedDomain>;
  astroliftOrganizations: Array<AstroliftOrganization>;
  astroliftPipeline?: Maybe<AstroliftPipeline>;
  astroliftPipelineRun?: Maybe<AstroliftPipelineRun>;
  astroliftPipelineRuns: Array<AstroliftPipelineRun>;
  astroliftPipelines: Array<AstroliftPipeline>;
  astroliftPlatformApiUrl: Scalars['String']['output'];
  astroliftPodResourceUsage?: Maybe<AstroliftPodResourceUsage>;
  astroliftPolicies: Array<AstroliftPolicy>;
  astroliftPreviewEnvironments: Array<AstroliftPreviewEnvironment>;
  astroliftProjects: Array<AstroliftProject>;
  astroliftProviderPlugins: Array<AstroliftProviderPlugin>;
  astroliftProviderRegions: Array<AstroliftProviderRegion>;
  astroliftQuotas: Array<AstroliftQuota>;
  astroliftRecentActivity: AstroliftActivityPage;
  astroliftRecentClusterWorkflows: Array<AstroliftClusterWorkflowRun>;
  astroliftRenderedManifest?: Maybe<AstroliftRenderedManifest>;
  astroliftRoleBindings: Array<AstroliftRoleBinding>;
  astroliftRoles: Array<AstroliftRole>;
  astroliftRolesICanGrant: Array<AstroliftRole>;
  astroliftScheduledJobRuns: Array<AstroliftScheduledJobRun>;
  astroliftSearchableUsers: Array<AstroliftSearchableUser>;
  astroliftSecretBundles: Array<AstroliftSecretBundle>;
  astroliftSecretChangeProposal?: Maybe<AstroliftSecretChangeProposal>;
  astroliftSecretChangeProposals: Array<AstroliftSecretChangeProposal>;
  /** Multi-install handshake. Returns version, capabilities, feature flags, install identity, and server time so a mobile / CLI / SDK client can decide which UI to render before logging in. */
  astroliftServerInfo: AstroliftServerInfo;
  astroliftSourceConnections: Array<AstroliftSourceConnection>;
  astroliftSourceFile: AstroliftSourceFile;
  astroliftSshDeployKeys: Array<AstroliftSshDeployKey>;
  astroliftTaskRuns: Array<AstroliftTaskRun>;
  astroliftTeamMembers: Array<AstroliftMember>;
  astroliftTeams: Array<AstroliftTeam>;
  astroliftTraceSpans: Array<AstroliftTraceSpan>;
  astroliftWebhookDeliveries: Array<AstroliftWebhookDelivery>;
  astroliftWebhookSubscriptions: Array<AstroliftWebhookSubscription>;
  astroliftWorkflowInstance?: Maybe<AstroliftWorkflowInstance>;
  astroliftWorkflowInstanceDetail?: Maybe<AstroliftWorkflowInstanceDetail>;
  astroliftWorkflowInstances: AstroliftWorkflowInstancePage;
  astroliftWorkflowRuns: Array<AstroliftWorkflowRun>;
  astroliftWorkload?: Maybe<AstroliftWorkload>;
  astroliftWorkloadManifest?: Maybe<AstroliftWorkloadManifest>;
  astroliftWorkloadPodStatusBreakdown: Array<AstroliftWorkloadPodStatusBucket>;
  astroliftWorkloadResourceUsage?: Maybe<AstroliftWorkloadResourceUsage>;
  astroliftWorkloadScalingStatus?: Maybe<AstroliftWorkloadScalingStatus>;
  astroliftWorkloads: Array<AstroliftWorkload>;
  /** Query mutation audit logs. Superuser only. */
  auditLogs: Array<AuditLogEntry>;
  brief?: Maybe<AstroliftBrief>;
  dispatchers: Array<AstroliftDispatcherInstance>;
  /** List all effective permissions for a user, with the groups that grant each one. */
  effectivePermissions: Array<PermissionEntry>;
  employees: EmployeesConnection;
  formDefinition?: Maybe<AstroliftFormDefinition>;
  formDefinitions: Array<AstroliftFormDefinition>;
  formFieldTypes: Array<Scalars['String']['output']>;
  formSubmissions: Array<AstroliftFormSubmission>;
  me?: Maybe<AstroliftMe>;
  members: Array<OrganizationMemberType>;
  orgToolDefs: Array<AstroliftToolDef>;
  organization?: Maybe<OrganizationType>;
  organizations: Array<OrganizationType>;
  /** Compare effective permissions between two users. */
  permissionCompare?: Maybe<PermissionComparison>;
  /** Diagnose why a user can or can't perform a specific permission. */
  permissionDiagnose?: Maybe<PermissionDiagnosis>;
  previewAstroliftDeregister?: Maybe<AstroliftDeregisterPreview>;
  previewAstroliftForceRedeploy?: Maybe<AstroliftForceRedeployPreview>;
  scanAgentManifests: AstroliftScanAgentManifestsResult;
  skill?: Maybe<AstroliftSkill>;
  skills: Array<AstroliftSkill>;
  toolDefs: Array<AstroliftToolDef>;
  /** Get a workflow definition by slug. */
  workflowDefinition?: Maybe<WorkflowDefinitionType>;
  /** List all workflow definitions. */
  workflowDefinitions: Array<WorkflowDefinitionType>;
  /** Get a workflow instance by ID. */
  workflowInstance?: Maybe<WorkflowInstanceType>;
  /** List workflow instances for a specific object. */
  workflowInstances: Array<WorkflowInstanceType>;
  /** List stage executions for a WorkflowRun (by workflow_id + run_id). */
  workflowStageExecutions: Array<WorkflowStageExecutionType>;
  /** List stages for a workflow definition by slug. */
  workflowStages: Array<WorkflowStageType>;
};


export type QueryAgentArgs = {
  orgId: Scalars['ID']['input'];
  slug: Scalars['String']['input'];
};


export type QueryAgentEnvironmentSpecArgs = {
  slug: Scalars['String']['input'];
};


export type QueryAgentEnvironmentSpecsArgs = {
  orgId: Scalars['ID']['input'];
};


export type QueryAgentFleetArgs = {
  orgId: Scalars['ID']['input'];
};


export type QueryAgentGalleryArgs = {
  orgId: Scalars['ID']['input'];
};


export type QueryAgentLiveStatusArgs = {
  orgId: Scalars['ID']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
  workloadId?: InputMaybe<Scalars['ID']['input']>;
};


export type QueryAgentTaskArgs = {
  id: Scalars['ID']['input'];
};


export type QueryAgentTaskLogsArgs = {
  id: Scalars['ID']['input'];
  tail?: Scalars['Int']['input'];
};


export type QueryAgentTasksArgs = {
  orgId: Scalars['ID']['input'];
  status?: InputMaybe<Scalars['String']['input']>;
  workloadId?: InputMaybe<Scalars['ID']['input']>;
};


export type QueryAgentWorkloadsArgs = {
  orgId: Scalars['ID']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAgentRunsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
  status?: InputMaybe<Scalars['String']['input']>;
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
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
  includeDrift?: Scalars['Boolean']['input'];
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


export type QueryAstroliftAppEndpointMetricsArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  rangeSeconds?: InputMaybe<Scalars['Int']['input']>;
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
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


export type QueryAstroliftAppLogsArgs = {
  appSlug: Scalars['String']['input'];
  cursor?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  level?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  search?: InputMaybe<Scalars['String']['input']>;
  since: Scalars['DateTime']['input'];
  until: Scalars['DateTime']['input'];
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppManagedServiceMetricsArgs = {
  managedServiceId: Scalars['ID']['input'];
  rangeSeconds?: InputMaybe<Scalars['Int']['input']>;
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


export type QueryAstroliftAppSecretHistoryArgs = {
  appSlug: Scalars['String']['input'];
  key: Scalars['String']['input'];
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


export type QueryAstroliftAppTracesArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
  minDurationMs?: InputMaybe<Scalars['Float']['input']>;
  operation?: InputMaybe<Scalars['String']['input']>;
  service?: InputMaybe<Scalars['String']['input']>;
  since: Scalars['String']['input'];
  status?: InputMaybe<Scalars['String']['input']>;
  until: Scalars['String']['input'];
};


export type QueryAstroliftAppUrlHealthArgs = {
  appSlug: Scalars['String']['input'];
  forceRefresh?: Scalars['Boolean']['input'];
  url: Scalars['String']['input'];
};


export type QueryAstroliftAppUrlProbeHistoryArgs = {
  appSlug: Scalars['String']['input'];
  limit?: Scalars['Int']['input'];
  url: Scalars['String']['input'];
};


export type QueryAstroliftAppsArgs = {
  includeFreshness?: Scalars['Boolean']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
  search?: InputMaybe<Scalars['String']['input']>;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftAppsPageArgs = {
  cursor?: InputMaybe<Scalars['String']['input']>;
  includeArchived?: Scalars['Boolean']['input'];
  includeFreshness?: Scalars['Boolean']['input'];
  limit?: Scalars['Int']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
  search?: InputMaybe<Scalars['String']['input']>;
  sortBy?: AppsListSortKey;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars['String']['input']>;
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


export type QueryAstroliftClusterCertificatesArgs = {
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


export type QueryAstroliftClusterLiveStateArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftClusterPrometheusMetricsArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftClusterPrometheusRangeMetricsArgs = {
  clusterId: Scalars['GUID']['input'];
  rangeSeconds?: Scalars['Int']['input'];
  stepSeconds?: Scalars['Int']['input'];
};


export type QueryAstroliftClusterWorkloadHealthArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftCognitoUserPoolClientsArgs = {
  clusterId: Scalars['GUID']['input'];
  poolId: Scalars['String']['input'];
};


export type QueryAstroliftCognitoUserPoolsArgs = {
  clusterId: Scalars['GUID']['input'];
};


export type QueryAstroliftCommandRunsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftCompareDeploymentsArgs = {
  idA: Scalars['String']['input'];
  idB: Scalars['String']['input'];
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


export type QueryAstroliftDeploymentReleaseNotesArgs = {
  deploymentId: Scalars['String']['input'];
};


export type QueryAstroliftDeploymentsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftDnsCertificatesArgs = {
  dnsDriver: Scalars['String']['input'];
};


export type QueryAstroliftDnsZonesArgs = {
  dnsDriver: Scalars['String']['input'];
};


export type QueryAstroliftEmailEngagementMetricsArgs = {
  days?: Scalars['Int']['input'];
  managedServiceId: Scalars['GUID']['input'];
};


export type QueryAstroliftEmailMessagesArgs = {
  eventKind?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  managedServiceId: Scalars['GUID']['input'];
  recipient?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftEmailServiceDetailArgs = {
  managedServiceId: Scalars['GUID']['input'];
};


export type QueryAstroliftEmailTemplateArgs = {
  managedServiceId: Scalars['GUID']['input'];
  name: Scalars['String']['input'];
};


export type QueryAstroliftEmailTemplateStatsArgs = {
  days?: Scalars['Int']['input'];
  managedServiceId: Scalars['GUID']['input'];
  name: Scalars['String']['input'];
};


export type QueryAstroliftEmailTemplatesArgs = {
  managedServiceId: Scalars['GUID']['input'];
};


export type QueryAstroliftEnvironmentsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftEventsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  eventType?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  severity?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftEventsAggregatedArgs = {
  aggregateWindowSeconds?: Scalars['Int']['input'];
  appSlug?: InputMaybe<Scalars['String']['input']>;
  eventType?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  severity?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftEventsPageArgs = {
  after?: InputMaybe<Scalars['String']['input']>;
  appSlug?: InputMaybe<Scalars['String']['input']>;
  eventType?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  severity?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftExecutePromqlArgs = {
  appSlug: Scalars['String']['input'];
  endUnix: Scalars['Int']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  query: Scalars['String']['input'];
  startUnix: Scalars['Int']['input'];
  stepSeconds: Scalars['Int']['input'];
};


export type QueryAstroliftInvitationsArgs = {
  status?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftManagedServiceObjectsArgs = {
  limit?: Scalars['Int']['input'];
  managedServiceId: Scalars['GUID']['input'];
};


export type QueryAstroliftManagedServiceQueueDepthArgs = {
  managedServiceId: Scalars['GUID']['input'];
};


export type QueryAstroliftManagedServicesArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMembersArgs = {
  search?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMyAlertSubscriptionsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMyAppsArgs = {
  includeFreshness?: Scalars['Boolean']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
  search?: InputMaybe<Scalars['String']['input']>;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMyAppsPageArgs = {
  cursor?: InputMaybe<Scalars['String']['input']>;
  includeArchived?: Scalars['Boolean']['input'];
  includeFreshness?: Scalars['Boolean']['input'];
  limit?: Scalars['Int']['input'];
  projectSlug?: InputMaybe<Scalars['String']['input']>;
  search?: InputMaybe<Scalars['String']['input']>;
  sortBy?: AppsListSortKey;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMyNotificationPreferencesArgs = {
  channel?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftMyNotificationsArgs = {
  limit?: Scalars['Int']['input'];
  unreadOnly?: Scalars['Boolean']['input'];
};


export type QueryAstroliftOrgMembersForApprovalPickerArgs = {
  orgSlug: Scalars['String']['input'];
};


export type QueryAstroliftOrganizationArgs = {
  slug: Scalars['String']['input'];
};


export type QueryAstroliftPipelineArgs = {
  id: Scalars['String']['input'];
};


export type QueryAstroliftPipelineRunArgs = {
  id: Scalars['String']['input'];
};


export type QueryAstroliftPipelineRunsArgs = {
  limit?: Scalars['Int']['input'];
  pipelineId: Scalars['String']['input'];
};


export type QueryAstroliftPipelinesArgs = {
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftPodResourceUsageArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  podName: Scalars['String']['input'];
  rangeSeconds?: InputMaybe<Scalars['Int']['input']>;
};


export type QueryAstroliftPreviewEnvironmentsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftProviderRegionsArgs = {
  providerPluginSlug: Scalars['String']['input'];
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


export type QueryAstroliftSearchableUsersArgs = {
  query: Scalars['String']['input'];
};


export type QueryAstroliftSecretChangeProposalArgs = {
  id: Scalars['GUID']['input'];
};


export type QueryAstroliftSecretChangeProposalsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  status?: InputMaybe<Scalars['String']['input']>;
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


export type QueryAstroliftTaskRunsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  status?: InputMaybe<Scalars['String']['input']>;
  workloadSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftTeamMembersArgs = {
  teamId: Scalars['GUID']['input'];
};


export type QueryAstroliftTraceSpansArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  traceId: Scalars['String']['input'];
};


export type QueryAstroliftWebhookDeliveriesArgs = {
  limit?: Scalars['Int']['input'];
  subscriptionId: Scalars['GUID']['input'];
};


export type QueryAstroliftWebhookSubscriptionsArgs = {
  appSlug?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftWorkflowInstanceArgs = {
  workflowId: Scalars['String']['input'];
};


export type QueryAstroliftWorkflowInstanceDetailArgs = {
  workflowId: Scalars['String']['input'];
};


export type QueryAstroliftWorkflowInstancesArgs = {
  after?: InputMaybe<Scalars['String']['input']>;
  limit?: Scalars['Int']['input'];
  status?: InputMaybe<Scalars['String']['input']>;
  workflowType?: InputMaybe<Scalars['String']['input']>;
};


export type QueryAstroliftWorkflowRunsArgs = {
  limit?: Scalars['Int']['input'];
};


export type QueryAstroliftWorkloadArgs = {
  appSlug: Scalars['String']['input'];
  slug: Scalars['String']['input'];
};


export type QueryAstroliftWorkloadManifestArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  imageTag?: InputMaybe<Scalars['String']['input']>;
  workloadSlug: Scalars['String']['input'];
};


export type QueryAstroliftWorkloadPodStatusBreakdownArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  workloadSlug: Scalars['String']['input'];
};


export type QueryAstroliftWorkloadResourceUsageArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
  workloadSlug: Scalars['String']['input'];
};


export type QueryAstroliftWorkloadScalingStatusArgs = {
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


export type QueryBriefArgs = {
  id: Scalars['ID']['input'];
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


export type QueryFormDefinitionArgs = {
  slug: Scalars['String']['input'];
};


export type QueryFormDefinitionsArgs = {
  status?: InputMaybe<Scalars['String']['input']>;
};


export type QueryFormSubmissionsArgs = {
  slug: Scalars['String']['input'];
  status?: InputMaybe<Scalars['String']['input']>;
};


export type QueryOrgToolDefsArgs = {
  orgId: Scalars['ID']['input'];
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


export type QueryPreviewAstroliftDeregisterArgs = {
  appSlug: Scalars['String']['input'];
};


export type QueryPreviewAstroliftForceRedeployArgs = {
  appSlug: Scalars['String']['input'];
  environmentName?: InputMaybe<Scalars['String']['input']>;
};


export type QueryScanAgentManifestsArgs = {
  orgId: Scalars['ID']['input'];
  ref?: Scalars['String']['input'];
  sourceKind?: Scalars['String']['input'];
  sourceRepo: Scalars['String']['input'];
};


export type QuerySkillArgs = {
  id: Scalars['ID']['input'];
};


export type QuerySkillsArgs = {
  isGlobal?: Scalars['Boolean']['input'];
  orgId: Scalars['ID']['input'];
};


export type QueryToolDefsArgs = {
  skillId: Scalars['ID']['input'];
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


export type QueryWorkflowStageExecutionsArgs = {
  runId: Scalars['String']['input'];
  workflowId: Scalars['String']['input'];
};


export type QueryWorkflowStagesArgs = {
  workflowSlug: Scalars['String']['input'];
};

export type RecheckDomainValidationInput = {
  id: Scalars['GUID']['input'];
};

export type ReconcileClusterIngressesInput = {
  clusterId: Scalars['GUID']['input'];
};

export type ReconcileClusterIngressesResult = {
  errors: Array<Scalars['String']['output']>;
  reconciledCount: Scalars['Int']['output'];
  skippedCount: Scalars['Int']['output'];
};

export type ReconcileClusterIngressesResultMutationResult = {
  data?: Maybe<ReconcileClusterIngressesResult>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
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

export type RegisterAgentRepoInput = {
  defaultBranch: InputMaybe<Scalars['String']['input']>;
  deployBranch: InputMaybe<Scalars['String']['input']>;
  projectId: Scalars['GUID']['input'];
  ref: Scalars['String']['input'];
  sourceKind: Scalars['String']['input'];
  sourceRepo: Scalars['String']['input'];
  sourceUrl: InputMaybe<Scalars['String']['input']>;
};

export type RegisterAppInput = {
  approverTeamId: InputMaybe<Scalars['GUID']['input']>;
  approverUserIds: InputMaybe<Array<Scalars['String']['input']>>;
  buildArgs: InputMaybe<Scalars['JSON']['input']>;
  buildContext: Scalars['String']['input'];
  buildMode: Scalars['String']['input'];
  buildStrategy: Scalars['String']['input'];
  cronExpression: InputMaybe<Scalars['String']['input']>;
  defaultBranch: InputMaybe<Scalars['String']['input']>;
  deployBranch: InputMaybe<Scalars['String']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  dockerfilePath: Scalars['String']['input'];
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

export type RegisterMobileDeviceInput = {
  deviceToken: Scalars['String']['input'];
  label: InputMaybe<Scalars['String']['input']>;
  platform: Scalars['String']['input'];
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

export type ReissueManagedDomainCertPayload = {
  message: Scalars['String']['output'];
  signaled: Scalars['Boolean']['output'];
  zone: Scalars['String']['output'];
};

export type ReissueManagedDomainCertPayloadMutationResult = {
  data?: Maybe<ReissueManagedDomainCertPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type RejectByTokenInput = {
  reason: InputMaybe<Scalars['String']['input']>;
  token: Scalars['String']['input'];
};

export type RejectSecretChangeInput = {
  proposalId: Scalars['GUID']['input'];
  reason: Scalars['String']['input'];
};

export type RemoveAppDomainInput = {
  id: Scalars['GUID']['input'];
};

export type RemoveEmailSuppressionEntryInput = {
  address: Scalars['String']['input'];
  managedServiceId: Scalars['GUID']['input'];
};

export type RemoveOrganizationAllowlistDomainInput = {
  id: Scalars['GUID']['input'];
};

export type ReprovisionManagedServiceInput = {
  managedServiceId: Scalars['GUID']['input'];
};

export type RequestAttestationChallengeInput = {
  kind: Scalars['String']['input'];
};

export type RequestQuotaIncreaseInput = {
  factor: Scalars['Float']['input'];
  quotaId: Scalars['GUID']['input'];
  reason: Scalars['String']['input'];
};

export type RestartWorkloadInput = {
  workloadId: Scalars['GUID']['input'];
};

export type RestoreAppInput = {
  appSlug: Scalars['String']['input'];
};

export type ResumeAppWebhookDeploysInput = {
  appSlug: Scalars['String']['input'];
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

export type RevalidateManagedDomainPayload = {
  message: Scalars['String']['output'];
  signaled: Scalars['Boolean']['output'];
  zone: Scalars['String']['output'];
};

export type RevalidateManagedDomainPayloadMutationResult = {
  data?: Maybe<RevalidateManagedDomainPayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type RevealAppSecretInput = {
  appSlug: Scalars['String']['input'];
  secretId: Scalars['String']['input'];
};

export type RevealManagedServiceConnectionInput = {
  managedServiceId: Scalars['GUID']['input'];
};

export type RevokeApiTokenInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeAppCertificateInput = {
  customDomainId: Scalars['GUID']['input'];
};

export type RevokeAstroliftSessionInput = {
  reason: InputMaybe<Scalars['String']['input']>;
  sessionId: Scalars['GUID']['input'];
};

export type RevokeDeployTokenInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeInvitationInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeMobileDeviceInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeRoleBindingInput = {
  id: Scalars['GUID']['input'];
};

export type RevokeTeamAccessInput = {
  appId: Scalars['GUID']['input'];
  teamId: Scalars['GUID']['input'];
};

export type Revokemobiledevicepayload = {
  id: Scalars['GUID']['output'];
  revoked: Scalars['Boolean']['output'];
};

export type RevokemobiledevicepayloadMutationResult = {
  data?: Maybe<Revokemobiledevicepayload>;
  errors: Array<MutationError>;
  ok: Scalars['Boolean']['output'];
};

export type RotateAppSecretInput = {
  appSlug: Scalars['String']['input'];
  expiresAt: InputMaybe<Scalars['DateTime']['input']>;
  ifMatchVersion: InputMaybe<Scalars['Int']['input']>;
  key: Scalars['String']['input'];
  scope: Scalars['String']['input'];
  setVia: InputMaybe<Scalars['String']['input']>;
  value: Scalars['String']['input'];
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

export type RunAstroliftAgentInput = {
  agentSlug: Scalars['String']['input'];
  environmentSpecId: InputMaybe<Scalars['GUID']['input']>;
  timeoutSeconds: InputMaybe<Scalars['Int']['input']>;
  triggerPayload: InputMaybe<Scalars['JSON']['input']>;
};

export type RunJobOnceInput = {
  appSlug: Scalars['String']['input'];
  environmentName: Scalars['String']['input'];
  jobSlug: Scalars['String']['input'];
};

export type RunTaskInput = {
  appSlug: Scalars['String']['input'];
  command: Array<Scalars['String']['input']>;
  environmentName: InputMaybe<Scalars['String']['input']>;
  workloadSlug: Scalars['String']['input'];
};

export type RunWorkflowDefinitionResult = {
  errors: Array<ValidationError>;
  ok: Scalars['Boolean']['output'];
  temporalWorkflowId?: Maybe<Scalars['String']['output']>;
  workflowRunId?: Maybe<Scalars['ID']['output']>;
};

export type ScaleWorkloadInput = {
  replicas: Scalars['Int']['input'];
  workloadId: Scalars['GUID']['input'];
};

export type SendManagedServiceTestEmailInput = {
  body: InputMaybe<Scalars['String']['input']>;
  managedServiceId: Scalars['GUID']['input'];
  recipient: Scalars['String']['input'];
  subject: InputMaybe<Scalars['String']['input']>;
};

export type SetActiveIdentityProviderInput = {
  id: Scalars['GUID']['input'];
};

export type SetAlertSubscriptionInput = {
  alertKind: Scalars['String']['input'];
  appSlug: Scalars['String']['input'];
  channel: Scalars['String']['input'];
  enabled: Scalars['Boolean']['input'];
};

export type SetAppSecretInput = {
  appSlug: Scalars['String']['input'];
  expiresAt: InputMaybe<Scalars['DateTime']['input']>;
  ifMatchVersion: InputMaybe<Scalars['Int']['input']>;
  key: Scalars['String']['input'];
  scope: Scalars['String']['input'];
  setVia: InputMaybe<Scalars['String']['input']>;
  value: Scalars['String']['input'];
};

export type SetAppSecretMetadataInput = {
  appSlug: Scalars['String']['input'];
  environmentName: InputMaybe<Scalars['String']['input']>;
  expiresAt: InputMaybe<Scalars['DateTime']['input']>;
  key: Scalars['String']['input'];
  scope: Scalars['String']['input'];
  setVia: InputMaybe<Scalars['String']['input']>;
};

export type SetAppSubdomainInput = {
  id: Scalars['GUID']['input'];
  subdomain: Scalars['String']['input'];
};

export type SetDomainPathRoutesInput = {
  domainId: Scalars['GUID']['input'];
  routes: Array<DomainPathRouteInput>;
};

export type SetDomainRedirectsInput = {
  domainId: Scalars['GUID']['input'];
  rules: Array<DomainRedirectRuleInput>;
};

export type SetEnvironmentSettingInput = {
  environmentId: Scalars['GUID']['input'];
  key: Scalars['String']['input'];
  value: Scalars['String']['input'];
};

export type SetNotificationPreferenceInput = {
  channel: Scalars['String']['input'];
  enabled: Scalars['Boolean']['input'];
  eventKind: Scalars['String']['input'];
};

export type SetRetentionPolicyInput = {
  appSlug: Scalars['String']['input'];
  retentionDays: Scalars['Int']['input'];
  signal: Scalars['String']['input'];
};

export type SharedDirectoryType = {
  directoryCount: Scalars['Int']['output'];
  fileCount: Scalars['Int']['output'];
};

export type SkillInput = {
  content: Scalars['String']['input'];
  dependencies: InputMaybe<Scalars['JSON']['input']>;
  description: Scalars['String']['input'];
  name: Scalars['String']['input'];
  slug: Scalars['String']['input'];
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
  commitAuthorAvatarUrl: InputMaybe<Scalars['String']['input']>;
  commitMessage: InputMaybe<Scalars['String']['input']>;
  commitSha: InputMaybe<Scalars['String']['input']>;
  environmentName: Scalars['String']['input'];
  imageDigest: InputMaybe<Scalars['String']['input']>;
  imageTag: Scalars['String']['input'];
  prNumber: InputMaybe<Scalars['Int']['input']>;
  strategy: InputMaybe<Scalars['String']['input']>;
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
  astroliftOnAppLogs: AstroliftAppLogLine;
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


export type SubscriptionAstroliftOnAppLogsArgs = {
  appSlug: Scalars['String']['input'];
  container?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  follow?: Scalars['Boolean']['input'];
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

export type ToolDefInput = {
  adapter: Scalars['String']['input'];
  description: Scalars['String']['input'];
  handlerRef: Scalars['String']['input'];
  implementationConfig: InputMaybe<Scalars['JSON']['input']>;
  inputSchema: Scalars['JSON']['input'];
  name: Scalars['String']['input'];
  outputSchema: Scalars['JSON']['input'];
  slug: Scalars['String']['input'];
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

export type UnmuteAlertRuleInput = {
  ruleId: Scalars['GUID']['input'];
};

export type UnregisterTenantClusterInput = {
  id: Scalars['GUID']['input'];
};

export type UpdateAgentEnvironmentSpecInput = {
  agentType: InputMaybe<Scalars['String']['input']>;
  allowInstall: InputMaybe<Scalars['Boolean']['input']>;
  configBranch: InputMaybe<Scalars['String']['input']>;
  configManifestPath: InputMaybe<Scalars['String']['input']>;
  configRepo: InputMaybe<Scalars['String']['input']>;
  envVars: InputMaybe<Scalars['JSON']['input']>;
  imageTag: InputMaybe<Scalars['String']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  runtime: InputMaybe<Scalars['String']['input']>;
  secretRefs: InputMaybe<Scalars['JSON']['input']>;
  toolPreset: InputMaybe<Scalars['String']['input']>;
  vncEnabled: InputMaybe<Scalars['Boolean']['input']>;
};

export type UpdateAlertRuleInput = {
  id: Scalars['GUID']['input'];
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  managedServiceId: InputMaybe<Scalars['GUID']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  notifyChannels: InputMaybe<Scalars['JSON']['input']>;
  predicate: InputMaybe<Scalars['JSON']['input']>;
  severity: InputMaybe<Scalars['String']['input']>;
};

export type UpdateAppInput = {
  approverTeamId: InputMaybe<Scalars['GUID']['input']>;
  approverUserIds: InputMaybe<Array<Scalars['String']['input']>>;
  buildArgs: InputMaybe<Scalars['JSON']['input']>;
  buildContext: InputMaybe<Scalars['String']['input']>;
  buildMode: InputMaybe<Scalars['String']['input']>;
  buildStrategy: InputMaybe<Scalars['String']['input']>;
  cronExpression: InputMaybe<Scalars['String']['input']>;
  cronPaused: InputMaybe<Scalars['Boolean']['input']>;
  defaultBranch: InputMaybe<Scalars['String']['input']>;
  deployBranch: InputMaybe<Scalars['String']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  dockerfilePath: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  ifMatchVersion: InputMaybe<Scalars['Int']['input']>;
  isActive: InputMaybe<Scalars['Boolean']['input']>;
  manifestPath: InputMaybe<Scalars['String']['input']>;
  minimumApprovals: InputMaybe<Scalars['Int']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  previewEnabled: InputMaybe<Scalars['Boolean']['input']>;
  requiresApproval: InputMaybe<Scalars['Boolean']['input']>;
  sourceUrl: InputMaybe<Scalars['String']['input']>;
  triggerMode: InputMaybe<Scalars['String']['input']>;
};

export type UpdateEmailTemplateInput = {
  htmlBody: Scalars['String']['input'];
  managedServiceId: Scalars['GUID']['input'];
  name: Scalars['String']['input'];
  subject: Scalars['String']['input'];
  textBody: Scalars['String']['input'];
};

export type UpdateIdentityProviderInput = {
  clientId: InputMaybe<Scalars['String']['input']>;
  clientSecretRef: InputMaybe<Scalars['String']['input']>;
  config: InputMaybe<Scalars['JSON']['input']>;
  displayName: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  ifMatchVersion: InputMaybe<Scalars['Int']['input']>;
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
  timezone: InputMaybe<Scalars['String']['input']>;
};

export type UpdateOrganizationInput = {
  allowUserProfileEdit: InputMaybe<Scalars['Boolean']['input']>;
  auditLogRetentionDays: InputMaybe<Scalars['Int']['input']>;
  id: Scalars['GUID']['input'];
  name: InputMaybe<Scalars['String']['input']>;
  website: InputMaybe<Scalars['String']['input']>;
};

export type UpdatePipelineInput = {
  defaultBranch: InputMaybe<Scalars['String']['input']>;
  name: InputMaybe<Scalars['String']['input']>;
  repoUrl: InputMaybe<Scalars['String']['input']>;
  tomlPath: InputMaybe<Scalars['String']['input']>;
};

export type UpdatePolicyInput = {
  actionPattern: InputMaybe<Scalars['String']['input']>;
  actorPattern: InputMaybe<Scalars['JSON']['input']>;
  conditions: InputMaybe<Scalars['JSON']['input']>;
  description: InputMaybe<Scalars['String']['input']>;
  effect: InputMaybe<Scalars['String']['input']>;
  id: Scalars['GUID']['input'];
  ifMatchVersion: InputMaybe<Scalars['Int']['input']>;
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
  appClientId: InputMaybe<Scalars['String']['input']>;
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
  albAuthConfig: InputMaybe<Scalars['JSON']['input']>;
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
  ifMatchVersion: InputMaybe<Scalars['Int']['input']>;
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

export type ValidateAstroliftCiSecretsInput = {
  appSlug: Scalars['String']['input'];
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

export type WithdrawSecretChangeInput = {
  proposalId: Scalars['GUID']['input'];
};

export type WorkflowDefinitionType = {
  activeInstanceCount: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  description?: Maybe<Scalars['String']['output']>;
  instanceCount: Scalars['Int']['output'];
  isEnabled: Scalars['Boolean']['output'];
  modelLabel: Scalars['String']['output'];
  name: Scalars['String']['output'];
  patternKind: Scalars['String']['output'];
  slug: Scalars['String']['output'];
  states: Scalars['JSON']['output'];
  transitions: Scalars['JSON']['output'];
  workflowStages: Array<WorkflowStageType>;
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

export type WorkflowStageExecutionType = {
  agentRunGuid?: Maybe<Scalars['String']['output']>;
  attemptNumber: Scalars['Int']['output'];
  createdAt: Scalars['DateTime']['output'];
  endedAt?: Maybe<Scalars['DateTime']['output']>;
  errorMessage: Scalars['String']['output'];
  failure?: Maybe<Scalars['JSON']['output']>;
  guid: Scalars['ID']['output'];
  output?: Maybe<Scalars['JSON']['output']>;
  stageGuid: Scalars['String']['output'];
  stageKind: Scalars['String']['output'];
  stageOrder: Scalars['Int']['output'];
  startedAt?: Maybe<Scalars['DateTime']['output']>;
  status: Scalars['String']['output'];
};

export type WorkflowStageType = {
  agentDefinitionGuid?: Maybe<Scalars['String']['output']>;
  agentDefinitionName?: Maybe<Scalars['String']['output']>;
  createdAt: Scalars['DateTime']['output'];
  fanOutCount?: Maybe<Scalars['Int']['output']>;
  guid: Scalars['ID']['output'];
  kind: Scalars['String']['output'];
  onFailure: Scalars['String']['output'];
  order: Scalars['Int']['output'];
  skillRefs: Scalars['JSON']['output'];
  timeoutSeconds: Scalars['Int']['output'];
};

export type CreateSkillMutationVariables = Exact<{
  orgId: Scalars['ID']['input'];
  input: SkillInput;
}>;


export type CreateSkillMutation = { createSkill: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, name: string, slug: string, isActive: boolean } | null } };

export type UpdateSkillMutationVariables = Exact<{
  id: Scalars['ID']['input'];
  input: SkillInput;
}>;


export type UpdateSkillMutation = { updateSkill: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, name: string, slug: string, isActive: boolean } | null } };

export type DeleteSkillMutationVariables = Exact<{
  id: Scalars['ID']['input'];
}>;


export type DeleteSkillMutation = { deleteSkill: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }> } };

export type CreateToolDefMutationVariables = Exact<{
  skillId: Scalars['ID']['input'];
  input: ToolDefInput;
}>;


export type CreateToolDefMutation = { createToolDef: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, name: string, slug: string, adapter: string } | null } };

export type UpdateToolDefMutationVariables = Exact<{
  id: Scalars['ID']['input'];
  input: ToolDefInput;
}>;


export type UpdateToolDefMutation = { updateToolDef: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, name: string, slug: string, adapter: string } | null } };

export type DeleteToolDefMutationVariables = Exact<{
  id: Scalars['ID']['input'];
}>;


export type DeleteToolDefMutation = { deleteToolDef: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }> } };

export type AssembleBriefMutationVariables = Exact<{
  skillIds: Array<Scalars['ID']['input']> | Scalars['ID']['input'];
  orgId: Scalars['ID']['input'];
  config?: InputMaybe<Scalars['JSON']['input']>;
}>;


export type AssembleBriefMutation = { assembleBrief: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { ok: boolean, briefId?: string | null } | null } };

export type LaunchTaskMutationVariables = Exact<{
  briefId: Scalars['ID']['input'];
  orgId: Scalars['ID']['input'];
  callbackUrl?: InputMaybe<Scalars['String']['input']>;
}>;


export type LaunchTaskMutation = { launchTask: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { ok: boolean, taskId?: string | null } | null } };

export type CancelTaskMutationVariables = Exact<{
  id: Scalars['ID']['input'];
}>;


export type CancelTaskMutation = { cancelTask: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }> } };

export type RunAgentMutationVariables = Exact<{
  input: RunAstroliftAgentInput;
}>;


export type RunAgentMutation = { runAstroliftAgent: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, status: string, createdAt: string } | null } };

export type RegisterAgentRepoMutationVariables = Exact<{
  input: RegisterAgentRepoInput;
}>;


export type RegisterAgentRepoMutation = { registerAgentRepo: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { agents: Array<{ manifestPath: string, slug: string, appId: string, workloadSlug: string, created: boolean }> } | null } };

export type UpdateAgentRunSpecMutationVariables = Exact<{
  agentSlug: Scalars['String']['input'];
  input: AgentRunSpecInput;
}>;


export type UpdateAgentRunSpecMutation = { updateAgentRunSpec: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, kind: string, runFamily: string, runMode: string, runCronExpression: string, runPaused: boolean, runMaxParallel?: number | null, replicas: number, scheduledScaleTo?: number | null, scaleUpCron: string, scaleDownCron: string } | null } };

export type ImportSkillsFromRepoMutationVariables = Exact<{
  repoUrl: Scalars['String']['input'];
  branch?: InputMaybe<Scalars['String']['input']>;
}>;


export type ImportSkillsFromRepoMutation = { importSkillsFromRepo: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { importedSkills: Array<string>, importedTools: Array<string>, sourceRef: string } | null } };

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


export type ListClustersQuery = { astroliftClusters: Array<{ id: string, slug: string, name: string, organizationSlug?: string | null, providerPluginSlug: string, region: string, endpoint: string, authMethod: string, ingressClass: string, albAuthConfig?: Record<string, unknown> | null, isActive: boolean, capabilities: Record<string, unknown>, capabilitiesProbedAt?: string | null, createdAt: string, lifecycle: string, lastManagementError: string, managedAt?: string | null, lastHeartbeatAt?: string | null, heartbeatIntervalSeconds: number, heartbeatStatus: string, heartbeatAgeSeconds?: number | null, agentProvisioned: boolean, lastBootstrapRun?: { id: string, status: string, chartVersion: string, installedReleases: Record<string, unknown>, cliVersion: string, errorMessage: string, startedAt: string, endedAt: string, triggeredByUsername?: string | null } | null }> };

export type UpdateTenantClusterMutationVariables = Exact<{
  input: UpdateTenantClusterInput;
}>;


export type UpdateTenantClusterMutation = { updateTenantCluster: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, ingressClass: string, albAuthConfig?: Record<string, unknown> | null } | null } };

export type ReconcileClusterIngressesMutationVariables = Exact<{
  input: ReconcileClusterIngressesInput;
}>;


export type ReconcileClusterIngressesMutation = { reconcileClusterIngresses: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { reconciledCount: number, skippedCount: number, errors: Array<string> } | null } };

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

export type ClusterLiveStateQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterLiveStateQuery = { astroliftClusterLiveState?: { clusterId: string, status: string, lastHeartbeatAt?: string | null, heartbeatAgeSeconds?: number | null, heartbeatIntervalSeconds: number, agentProvisioned: boolean, nodeCount?: number | null, nodeReadyCount?: number | null, appReadiness: Record<string, unknown>, cpuUtilization?: number | null, memoryUtilization?: number | null, podTotal?: number | null, podsByNamespace: Record<string, unknown>, ingressIps: Array<string>, agentVersion: string } | null };

export type IssueClusterAgentKeyMutationVariables = Exact<{
  input: IssueClusterAgentKeyInput;
}>;


export type IssueClusterAgentKeyMutation = { issueClusterAgentKey: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { clusterId: string, agentKey: string, intervalSeconds: number, heartbeatUrl: string, rotated: boolean } | null } };

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

export type DeployClusterAgentMutationVariables = Exact<{
  input: DeployClusterAgentInput;
}>;


export type DeployClusterAgentMutation = { deployClusterAgent: { ok: boolean, errors: Array<{ code: string, message: string, field?: string | null }>, data?: { id: string, slug: string, agentProvisioned: boolean, heartbeatStatus: string } | null } };

export type ListProviderPluginsQueryVariables = Exact<{ [key: string]: never; }>;


export type ListProviderPluginsQuery = { astroliftProviderPlugins: Array<{ id: string, slug: string, name: string, version: string, capabilitiesManifest: Record<string, unknown>, isEnabled: boolean }> };

export type ProviderRegionsQueryVariables = Exact<{
  providerPluginSlug: Scalars['String']['input'];
}>;


export type ProviderRegionsQuery = { astroliftProviderRegions: Array<{ id: string, label: string, continent: string }> };

export type CognitoUserPoolsQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type CognitoUserPoolsQuery = { astroliftCognitoUserPools: Array<{ poolId: string, poolArn: string, name: string, domain: string, region: string }> };

export type CognitoUserPoolClientsQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
  poolId: Scalars['String']['input'];
}>;


export type CognitoUserPoolClientsQuery = { astroliftCognitoUserPoolClients: Array<{ clientId: string, clientName: string }> };

export type ClusterWorkloadHealthQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterWorkloadHealthQuery = { astroliftClusterWorkloadHealth: Array<{ namespace: string, workloadName: string, desiredReplicas: number, readyReplicas: number, restartCount24h: number, lastImageDeployedAt: string }> };

export type ClusterPrometheusMetricsQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterPrometheusMetricsQuery = { astroliftClusterPrometheusMetrics: { available: boolean, reason?: string | null, nodeCount?: number | null, podRunningRatio?: number | null, cpuUtilization?: number | null, memoryUtilization?: number | null, deploymentReadyRatio?: number | null } };

export type ClusterPrometheusRangeMetricsQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
  rangeSeconds?: InputMaybe<Scalars['Int']['input']>;
  stepSeconds?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ClusterPrometheusRangeMetricsQuery = { astroliftClusterPrometheusRangeMetrics: { available: boolean, reason?: string | null, rangeSeconds: number, stepSeconds: number, series: Array<{ metric: string, label: string, unit: string, current?: number | null, points: Array<{ ts: number, value: number }> }> } };

export type ClusterCertificatesQueryVariables = Exact<{
  clusterId: Scalars['GUID']['input'];
}>;


export type ClusterCertificatesQuery = { astroliftClusterCertificates: { supported: boolean, certificates: Array<{ arn: string, name: string, domainName: string, status: string }> } };

export type DnsZonesQueryVariables = Exact<{
  dnsDriver: Scalars['String']['input'];
}>;


export type DnsZonesQuery = { astroliftDnsZones: { supported: boolean, zones: Array<{ id: string, name: string, private: boolean, configJson: string }> } };

export type DnsCertificatesQueryVariables = Exact<{
  dnsDriver: Scalars['String']['input'];
}>;


export type DnsCertificatesQuery = { astroliftDnsCertificates: { supported: boolean, certificates: Array<{ arn: string, name: string, domainName: string, status: string }> } };

export type ListEnvironmentsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListEnvironmentsQuery = { astroliftEnvironments: Array<{ id: string, name: string, url: string, deploysPaused: boolean, ingressPaused: boolean, requiredApprovals: number, registeredAppSlug: string, clusterSlug?: string | null, clusterId?: string | null, clusterProviderPluginSlug?: string | null, domainZone?: string | null, createdAt: string, settings: Array<{ id: string, key: string, value: string }> }> };

export type ListDeploymentsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
  environmentName?: InputMaybe<Scalars['String']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListDeploymentsQuery = { astroliftDeployments: Array<{ id: string, registeredAppSlug: string, environmentName: string, workloadSlug?: string | null, triggerKind: string, strategy: string, status: string, imageTag: string, imageDigest: string, clusterRevision: string, approvalsRequired: number, approvalsReceived: number, requiredApproverCount: number, startedAt?: string | null, succeededAt?: string | null, failedAt?: string | null, endedAt?: string | null, durationSeconds?: number | null, createdAt: string, commitSha: string, commitMessage: string, commitAuthor: string, commitAuthorAvatarUrl: string, branch: string, prNumber: number, prUrl: string, ciActorKind: string, ciProvider: string, ciRunUrl: string, repoUrl: string, abortedReason: string, triggeredByUserId?: string | null, triggeredByMe: boolean, approvedBy: Array<{ userId: string, displayName: string, email: string, approvedAt?: string | null, mailtoUrl: string }>, awaitingApprovers: Array<{ userId: string, displayName: string, email: string, approvedAt?: string | null, mailtoUrl: string }> }> };

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


export type ListAppDomainsQuery = { astroliftAppDomains: Array<{ id: string, hostname: string, certState: string, validationMethod: string, validationToken: string, lastCheckedAt?: string | null, isActive: boolean, registeredAppSlug: string, createdAt: string, txtChallengeToken: string, expectedCnameTarget: string, isPlatformManagedZone: boolean, lastValidationError: string, certificateState: string, lastCertificateError: string, byoCertificateUploadedAt?: string | null, certExpiresAt?: string | null, certIssuerSerial: string, certObservabilityStatus: string, isWildcard: boolean, sniCertRef: string, requiredDnsRecords: Array<{ kind: string, name: string, value: string, ttl: number, propagated: boolean, lastCheckedAt?: string | null, message: string }>, redirectRules: Array<{ id: string, kind: string, sourcePattern: string, destinationUrl: string, httpStatus: number, preserveQueryString: boolean, priority: number }>, pathRoutes: Array<{ id: string, pathPrefix: string, targetWorkloadSlug: string, targetPort: number, stripPrefix: boolean, priority: number }> }> };

export type ListAppDeployTokensQueryVariables = Exact<{
  appSlug: Scalars['String']['input'];
}>;


export type ListAppDeployTokensQuery = { astroliftAppDeployTokens: Array<{ id: string, name: string, last4: string, scopes: Array<string>, expiresAt?: string | null, lastUsedAt?: string | null, lastUsedIp: string, lastUsedAgent: string, isRevoked: boolean, lastRotatedAt?: string | null, createdAt: string }> };

export type ListPreviewEnvironmentsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListPreviewEnvironmentsQuery = { astroliftPreviewEnvironments: Array<{ id: string, registeredAppSlug: string, prNumber: number, branch: string, commitSha: string, status: string, hostname: string, namespace: string, lastDeployedAt?: string | null, tornDownAt?: string | null, ttlUntil: string, sourceUrl: string, prUrl: string, isManual: boolean, estimatedDailyCostUsd?: number | null, aggregateResources: { cpuCores: number, memoryBytes: number, podCount: number } }> };

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

export type CompareDeploymentsQueryVariables = Exact<{
  idA: Scalars['String']['input'];
  idB: Scalars['String']['input'];
}>;


export type CompareDeploymentsQuery = { astroliftCompareDeployments?: { deploymentAId: string, deploymentBId: string, baseSha: string, headSha: string, compareUrl: string, imageDiffSummary: string, manifestDiff: Array<{ op: string, path: string, before: Record<string, unknown>, after: Record<string, unknown> }> } | null };

export type ListMyAlertSubscriptionsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
}>;


export type ListMyAlertSubscriptionsQuery = { astroliftMyAlertSubscriptions: Array<{ id: string, appSlug: string, alertKind: string, channel: string, enabled: boolean }> };

export type ListTaskRunsQueryVariables = Exact<{
  appSlug?: InputMaybe<Scalars['String']['input']>;
  limit?: InputMaybe<Scalars['Int']['input']>;
}>;


export type ListTaskRunsQuery = { astroliftTaskRuns: Array<{ id: string, registeredAppSlug: string, workloadSlug: string, triggerKind: string, triggeredByUsername?: string | null, command: Record<string, unknown>, status: string, exitCode?: number | null, startedAt?: string | null, endedAt?: string | null, durationSeconds?: number | null, k8sJobName: string, createdAt: string }> };

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
  appSlug?: InputMaybe<Scalars['String']['input']>;
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


export type ListAppSecretsQuery = { astroliftAppSecrets: Array<{ id: string, key: string, environmentName: string, source: string, bundleSlug: string, managedServiceKind: string, isMasked: boolean, lastEditedAt?: string | null, expiresAt?: string | null, setVia: string, scope: string, lastEditedBy?: { id: string, username: string, displayName: string } | null }> };

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

export type GetEmailTemplatesQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
}>;


export type GetEmailTemplatesQuery = { astroliftEmailTemplates: Array<{ name: string, subject: string, htmlBody: string, textBody: string, createdAt?: string | null }> };

export type GetEmailTemplateStatsQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
  name: Scalars['String']['input'];
  days?: InputMaybe<Scalars['Int']['input']>;
}>;


export type GetEmailTemplateStatsQuery = { astroliftEmailTemplateStats: Array<{ timestamp: string, sends: number, deliveries: number, bounces: number, complaints: number }> };

export type GetEmailMessagesQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
  limit?: InputMaybe<Scalars['Int']['input']>;
  eventKind?: InputMaybe<Scalars['String']['input']>;
  recipient?: InputMaybe<Scalars['String']['input']>;
}>;


export type GetEmailMessagesQuery = { astroliftEmailMessages: Array<{ id: string, messageId: string, recipient: string, subject: string, eventKind: string, occurredAt: string, metadata: Record<string, unknown> }> };

export type GetEmailEngagementMetricsQueryVariables = Exact<{
  managedServiceId: Scalars['GUID']['input'];
  days?: InputMaybe<Scalars['Int']['input']>;
}>;


export type GetEmailEngagementMetricsQuery = { astroliftEmailEngagementMetrics?: { totalSends: number, totalDeliveries: number, totalBounces: number, totalComplaints: number, totalOpens: number, totalClicks: number, bounceRatePct: number, complaintRatePct: number, openRatePct: number, clickRatePct: number, windowDays: number } | null };

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

export type UploadFileMutationVariables = Exact<{
  mimetype: Scalars['String']['input'];
  name?: InputMaybe<Scalars['String']['input']>;
}>;


export type UploadFileMutation = { fileUpload: { id?: string | null, preSignedUrl?: string | null, publicUrl?: string | null } };

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
