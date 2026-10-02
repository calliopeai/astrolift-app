export type Maybe<T> = T | null;
export type InputMaybe<T> = Maybe<T>;
export type Exact<T extends { [key: string]: unknown }> = { [K in keyof T]: T[K] };
export type MakeOptional<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]?: Maybe<T[SubKey]> };
export type MakeMaybe<T, K extends keyof T> = Omit<T, K> & { [SubKey in K]: Maybe<T[SubKey]> };
export type MakeEmpty<T extends { [key: string]: unknown }, K extends keyof T> = {
  [_ in K]?: never;
};
export type Incremental<T> =
  | T
  | { [P in keyof T]?: P extends " $fragmentName" | "__typename" ? T[P] : never };
/** All built-in and custom scalars, mapped to their actual values */
export type Scalars = {
  ID: { input: string; output: string };
  String: { input: string; output: string };
  Boolean: { input: boolean; output: boolean };
  Int: { input: number; output: number };
  Float: { input: number; output: number };
  /** Date (isoformat) */
  Date: { input: any; output: any };
  /** Date with time (isoformat) */
  DateTime: { input: string; output: string };
  /** UUID v7 — the platform's external identifier. */
  GUID: { input: string; output: string };
  /** The `JSON` scalar type represents JSON values as specified by [ECMA-404](https://ecma-international.org/wp-content/uploads/ECMA-404_2nd_edition_december_2017.pdf). */
  JSON: { input: Record<string, unknown>; output: Record<string, unknown> };
  UUID: { input: any; output: any };
  /** Represents NULL values */
  Void: { input: any; output: any };
};

export type AbortDeploymentInput = {
  id: Scalars["GUID"]["input"];
  reason: Scalars["String"]["input"];
};

export type AcceptInvitationInput = {
  token: Scalars["String"]["input"];
};

export type AcknowledgeAlertEventInput = {
  id: Scalars["GUID"]["input"];
};

export type AddAppDomainInput = {
  appSlug: Scalars["String"]["input"];
  hostname: Scalars["String"]["input"];
  validationMethod: InputMaybe<Scalars["String"]["input"]>;
};

export type AddEmailSuppressionEntryInput = {
  address: Scalars["String"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  note: Scalars["String"]["input"];
  reason: Scalars["String"]["input"];
};

export type AddOrganizationAllowlistDomainInput = {
  defaultRoleSlug: InputMaybe<Scalars["String"]["input"]>;
  domain: Scalars["String"]["input"];
  requiresReview: Scalars["Boolean"]["input"];
};

export type AddWildcardDomainInput = {
  appSlug: Scalars["String"]["input"];
  hostname: Scalars["String"]["input"];
  sniCertRef: Scalars["String"]["input"];
  validationMethod: Scalars["String"]["input"];
};

export type AdoptManagedResourceInput = {
  acknowledgedPriorOwner: Scalars["String"]["input"];
  id: Scalars["GUID"]["input"];
  reason: Scalars["String"]["input"];
  resourceId: Scalars["String"]["input"];
};

export type AgentRunFamily = "SERVICE" | "TASK";

export type AgentRunMode = "LOOP" | "ONCE" | "SCHEDULE" | "TRIGGER";

export type AgentRunSpecInput = {
  clearScheduledScaling: Scalars["Boolean"]["input"];
  replicas: InputMaybe<Scalars["Int"]["input"]>;
  runCronExpression: InputMaybe<Scalars["String"]["input"]>;
  runFamily: InputMaybe<AgentRunFamily>;
  runMaxParallel: InputMaybe<Scalars["Int"]["input"]>;
  runMode: InputMaybe<AgentRunMode>;
  runPaused: InputMaybe<Scalars["Boolean"]["input"]>;
  scaleDownCron: InputMaybe<Scalars["String"]["input"]>;
  scaleUpCron: InputMaybe<Scalars["String"]["input"]>;
  scheduledScaleTo: InputMaybe<Scalars["Int"]["input"]>;
};

export type AgentTaskCallbackMode = "FULL" | "NOTIFY";

export type AgentTaskCallbackPolicy = {
  allowedHosts: Array<Scalars["String"]["output"]>;
};

export type AgentTaskCallbackPolicyMutationResult = {
  data?: Maybe<AgentTaskCallbackPolicy>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AgentTaskCallbackSecret = {
  name: Scalars["String"]["output"];
};

export type AgentTaskCallbackSecretMutationResult = {
  data?: Maybe<AgentTaskCallbackSecret>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Alertruledeletedpayload = {
  deleted: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
};

export type AlertruledeletedpayloadMutationResult = {
  data?: Maybe<Alertruledeletedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AppDependencyCluster = {
  heartbeatObservedAt?: Maybe<Scalars["DateTime"]["output"]>;
  heartbeatSource: Scalars["String"]["output"];
  heartbeatState: DependencyObservationState;
  heartbeatStatus: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  lifecycle: Scalars["String"]["output"];
  providerId: Scalars["GUID"]["output"];
  providerSlug: Scalars["String"]["output"];
  region?: Maybe<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
};

export type AppDependencyContext = {
  appId: Scalars["GUID"]["output"];
  cluster: AppDependencyCluster;
  domainLimit: Scalars["Int"]["output"];
  domains: Array<AppDependencyDomain>;
  domainsScope: Scalars["String"]["output"];
  domainsState: DependencyObservationState;
  domainsTruncated: Scalars["Boolean"]["output"];
  environmentId: Scalars["GUID"]["output"];
  environmentName: Scalars["String"]["output"];
  liveProviderObservationReason: Scalars["String"]["output"];
  liveProviderObservationState: DependencyObservationState;
  managedDomain?: Maybe<AppDependencyManagedDomain>;
  managedDomainState: DependencyObservationState;
  permissions: AppDependencyPermissions;
  readAt: Scalars["DateTime"]["output"];
};

export type AppDependencyDomain = {
  certificateMetadataState: DependencyObservationState;
  certificateObservedAt?: Maybe<Scalars["DateTime"]["output"]>;
  certificateSource: Scalars["String"]["output"];
  configuredCertificateReferencePresent: Scalars["Boolean"]["output"];
  hostname: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  storedCertificateExpiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  storedCertificateRenewalStatus?: Maybe<Scalars["String"]["output"]>;
  storedCertificateState: Scalars["String"]["output"];
  validationObservedAt?: Maybe<Scalars["DateTime"]["output"]>;
  validationStatus: Scalars["String"]["output"];
};

export type AppDependencyManagedDomain = {
  id: Scalars["GUID"]["output"];
  zone: Scalars["String"]["output"];
};

export type AppDependencyPermissions = {
  appDeployGateAllowed: Scalars["Boolean"]["output"];
  appReadAllowed: Scalars["Boolean"]["output"];
  clusterRegisterGateAllowed: Scalars["Boolean"]["output"];
  requiredPermission: Scalars["String"]["output"];
};

export type Appdomainremovedpayload = {
  deleted: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
};

export type AppdomainremovedpayloadMutationResult = {
  data?: Maybe<Appdomainremovedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ApplyStagedManifestInput = {
  expectedStagedHash: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
};

export type ApproveByTokenInput = {
  token: Scalars["String"]["input"];
};

export type ApproveSecretChangeInput = {
  proposalId: Scalars["GUID"]["input"];
  reason: InputMaybe<Scalars["String"]["input"]>;
};

export type AppsListSortKey = "CREATED_DESC" | "DEPLOYED_DESC" | "NAME_ASC";

export type Appsecretmetadatapayload = {
  appSlug: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  key: Scalars["String"]["output"];
  pendingProposalId?: Maybe<Scalars["GUID"]["output"]>;
  scope: Scalars["String"]["output"];
  setAt?: Maybe<Scalars["DateTime"]["output"]>;
  setVia: Scalars["String"]["output"];
};

export type AppsecretmetadatapayloadMutationResult = {
  data?: Maybe<Appsecretmetadatapayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Appsecretwritepayload = {
  appSlug: Scalars["String"]["output"];
  key: Scalars["String"]["output"];
  pendingProposalId?: Maybe<Scalars["GUID"]["output"]>;
  rawManifestStaged: Scalars["String"]["output"];
};

export type AppsecretwritepayloadMutationResult = {
  data?: Maybe<Appsecretwritepayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ArchiveAppInput = {
  appSlug: Scalars["String"]["input"];
};

export type ArchiveAppRegistryRepoInput = {
  appId: Scalars["GUID"]["input"];
  archive: Scalars["Boolean"]["input"];
};

export type AssertSessionInput = {
  assertion: Scalars["String"]["input"];
  challenge: Scalars["String"]["input"];
};

export type AssignAppToProjectInput = {
  appSlug: Scalars["String"]["input"];
  projectGuid: InputMaybe<Scalars["GUID"]["input"]>;
};

export type AstroliftAccessEntry = {
  /** The share level on TEAM_SHARE rows. */
  accessLevel?: Maybe<Scalars["String"]["output"]>;
  /** The RoleBinding, GroupRoleMapping or AppTeamAccess share that grants it. */
  bindingId: Scalars["GUID"]["output"];
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  groupExternalId?: Maybe<Scalars["String"]["output"]>;
  groupMemberCount?: Maybe<Scalars["Int"]["output"]>;
  /** Held on an ancestor (or through a share), not on the object. */
  inherited: Scalars["Boolean"]["output"];
  inherits: Scalars["Boolean"]["output"];
  /** The user's ORG membership row. */
  memberId?: Maybe<Scalars["GUID"]["output"]>;
  /** USER, GROUP or TEAM. */
  principalKind: Scalars["String"]["output"];
  /** Null on a team's share row itself. */
  role?: Maybe<AstroliftRole>;
  scopeGuid?: Maybe<Scalars["GUID"]["output"]>;
  /** Where the grant is held. */
  scopeKind: Scalars["String"]["output"];
  shareId?: Maybe<Scalars["GUID"]["output"]>;
  /** USER_BINDING, GROUP_BINDING, GROUP_MAPPING, or TEAM_SHARE (a team's share on the app, or a binding on that team reaching the app through the share). */
  source: Scalars["String"]["output"];
  sourceScopeLabel: Scalars["String"]["output"];
  teamId?: Maybe<Scalars["GUID"]["output"]>;
  teamName?: Maybe<Scalars["String"]["output"]>;
  teamSlug?: Maybe<Scalars["String"]["output"]>;
  user?: Maybe<AstroliftUser>;
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAccessEntryPage = {
  items: Array<AstroliftAccessEntry>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftActionPermission = {
  allowed: Scalars["Boolean"]["output"];
  code: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
};

export type AstroliftActiveSession = {
  attestationKind: Scalars["String"]["output"];
  attestationTrustLevel: Scalars["String"]["output"];
  attestedAt?: Maybe<Scalars["DateTime"]["output"]>;
  clientKind: Scalars["String"]["output"];
  createdAt?: Maybe<Scalars["DateTime"]["output"]>;
  elevatedUntil?: Maybe<Scalars["DateTime"]["output"]>;
  elevationMethod?: Maybe<Scalars["String"]["output"]>;
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["String"]["output"];
  ipAddress?: Maybe<Scalars["String"]["output"]>;
  isCurrent: Scalars["Boolean"]["output"];
  label: Scalars["String"]["output"];
  lastSeenAt?: Maybe<Scalars["DateTime"]["output"]>;
  userAgent?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftActivityItem = {
  action: Scalars["String"]["output"];
  actorDisplay: Scalars["String"]["output"];
  eventType: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  payload: Scalars["JSON"]["output"];
  targetHref?: Maybe<Scalars["String"]["output"]>;
  targetKind: Scalars["String"]["output"];
  targetLabel: Scalars["String"]["output"];
};

export type AstroliftActivityPage = {
  items: Array<AstroliftActivityItem>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftAgentBox = {
  agentSlug: Scalars["String"]["output"];
  attachCommand: Array<Scalars["String"]["output"]>;
  createdAt: Scalars["DateTime"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentSpecSlug: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  idleTimeoutSeconds: Scalars["Int"]["output"];
  image: Scalars["String"]["output"];
  lastAttachedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastError: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
  ownerEmail: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  sessionName: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  startupDiagnostic?: Maybe<AstroliftAgentStartupDiagnostic>;
  status: Scalars["String"]["output"];
};

export type AstroliftAgentBoxMutationResult = {
  data?: Maybe<AstroliftAgentBox>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentDetail = {
  appSlug: Scalars["String"]["output"];
  brief?: Maybe<AstroliftBrief>;
  dockerfilePath: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  imageRef: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  replicas: Scalars["Int"]["output"];
  runCronExpression: Scalars["String"]["output"];
  runFamily: Scalars["String"]["output"];
  runMode: Scalars["String"]["output"];
  runPaused: Scalars["Boolean"]["output"];
  scaleDownCron: Scalars["String"]["output"];
  scaleUpCron: Scalars["String"]["output"];
  scheduledScaleTo?: Maybe<Scalars["Int"]["output"]>;
  skills: Array<AstroliftAgentSkill>;
  slug: Scalars["String"]["output"];
  sourceRepo: Scalars["String"]["output"];
};

export type AstroliftAgentEnvironmentSpec = {
  agentType: Scalars["String"]["output"];
  allowInstall: Scalars["Boolean"]["output"];
  boxWorkspace: Scalars["Boolean"]["output"];
  configBranch: Scalars["String"]["output"];
  configManifestPath: Scalars["String"]["output"];
  configRepo: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  envVars: Scalars["JSON"]["output"];
  gpu: Scalars["Int"]["output"];
  gpuType: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  imageTag: Scalars["String"]["output"];
  managedModel: Scalars["Boolean"]["output"];
  migProfile: Scalars["String"]["output"];
  modelGateway: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  projectId?: Maybe<Scalars["GUID"]["output"]>;
  runAsNonRoot: Scalars["Boolean"]["output"];
  runtime: Scalars["String"]["output"];
  secretRefs: Scalars["JSON"]["output"];
  slug: Scalars["String"]["output"];
  teamId?: Maybe<Scalars["GUID"]["output"]>;
  toolPreset: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  vncEnabled: Scalars["Boolean"]["output"];
};

export type AstroliftAgentEnvironmentSpecMutationResult = {
  data?: Maybe<AstroliftAgentEnvironmentSpec>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentEnvironmentSpecPage = {
  items: Array<AstroliftAgentEnvironmentSpec>;
  page: Scalars["Int"]["output"];
  pageSize: Scalars["Int"]["output"];
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftAgentEnvironmentSpecsFilter = {
  agentType: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** User ids, or "me". */
  createdBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  runtime: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftAgentFleetFilter = {
  /** Cluster slugs; an agent whose app has an environment on one matches. */
  cluster: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** managed, gateway or api-key (the row's modelSource). */
  model: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** User ids, or "me" for the viewer (the Mine view). */
  owner: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The run-spec pause switch. */
  paused: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Project slugs. */
  project: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The spec's runtime, or the run family (task, service). */
  runtime: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** running, failing, scheduled, paused or idle (the row's status). */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftAgentInteraction = {
  detail: Scalars["JSON"]["output"];
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftAgentListItem = {
  appSlug: Scalars["String"]["output"];
  clusterSlugs: Array<Scalars["String"]["output"]>;
  environmentSpecSlug: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  lastRunAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastRunStatus?: Maybe<Scalars["String"]["output"]>;
  modelSource?: Maybe<Scalars["String"]["output"]>;
  name: Scalars["String"]["output"];
  ownedByMe: Scalars["Boolean"]["output"];
  ownerEmail: Scalars["String"]["output"];
  projectSlug: Scalars["String"]["output"];
  replicas: Scalars["Int"]["output"];
  runCronExpression: Scalars["String"]["output"];
  runFamily: Scalars["String"]["output"];
  runMaxParallel?: Maybe<Scalars["Int"]["output"]>;
  runMode: Scalars["String"]["output"];
  runPaused: Scalars["Boolean"]["output"];
  runningCount: Scalars["Int"]["output"];
  runtime: Scalars["String"]["output"];
  scaleDownCron: Scalars["String"]["output"];
  scaleUpCron: Scalars["String"]["output"];
  scheduledScaleTo?: Maybe<Scalars["Int"]["output"]>;
  slug: Scalars["String"]["output"];
  sourceRepo: Scalars["String"]["output"];
  sourceUrl: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftAgentListItemPage = {
  items: Array<AstroliftAgentListItem>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  page?: Maybe<Scalars["Int"]["output"]>;
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAgentLiveStatus = {
  appSlug: Scalars["String"]["output"];
  deploymentReady?: Maybe<Scalars["Boolean"]["output"]>;
  desiredReplicas?: Maybe<Scalars["Int"]["output"]>;
  isIdle: Scalars["Boolean"]["output"];
  isPaused: Scalars["Boolean"]["output"];
  lastRunAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastRunStatus?: Maybe<Scalars["String"]["output"]>;
  nextScheduledAt?: Maybe<Scalars["DateTime"]["output"]>;
  readyReplicas?: Maybe<Scalars["Int"]["output"]>;
  runFamily: Scalars["String"]["output"];
  runMode: Scalars["String"]["output"];
  runningCount: Scalars["Int"]["output"];
  workloadId: Scalars["GUID"]["output"];
  workloadSlug: Scalars["String"]["output"];
};

export type AstroliftAgentQuarantine = {
  createdAt: Scalars["DateTime"]["output"];
  evidenceUrl: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  policyId: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
  targetId: Scalars["GUID"]["output"];
  targetKind: Scalars["String"]["output"];
};

export type AstroliftAgentRun = {
  createdAt: Scalars["DateTime"]["output"];
  durationSeconds?: Maybe<Scalars["Int"]["output"]>;
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  input?: Maybe<Scalars["JSON"]["output"]>;
  k8sPodName: Scalars["String"]["output"];
  output?: Maybe<Scalars["JSON"]["output"]>;
  reasoningTraceUrl: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  resultTtlHours: Scalars["Int"]["output"];
  retryCount: Scalars["Int"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  toolCallsCount: Scalars["Int"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggeredByUsername?: Maybe<Scalars["String"]["output"]>;
  workloadSlug: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAgentRunPage = {
  items: Array<AstroliftAgentRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAgentRunSpec = {
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  replicas: Scalars["Int"]["output"];
  runCronExpression: Scalars["String"]["output"];
  runFamily: Scalars["String"]["output"];
  runMaxParallel?: Maybe<Scalars["Int"]["output"]>;
  runMode: Scalars["String"]["output"];
  runPaused: Scalars["Boolean"]["output"];
  scaleDownCron: Scalars["String"]["output"];
  scaleUpCron: Scalars["String"]["output"];
  scheduledScaleTo?: Maybe<Scalars["Int"]["output"]>;
  slug: Scalars["String"]["output"];
};

export type AstroliftAgentRunSpecMutationResult = {
  data?: Maybe<AstroliftAgentRunSpec>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentRuntime = {
  image: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
};

export type AstroliftAgentScaleResult = {
  desiredReplicas?: Maybe<Scalars["Int"]["output"]>;
  message: Scalars["String"]["output"];
  ok: Scalars["Boolean"]["output"];
  readyReplicas?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAgentSecretBundle = {
  backendRef: Scalars["String"]["output"];
  canReveal: Scalars["Boolean"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  keyNames: Array<Scalars["String"]["output"]>;
  name: Scalars["String"]["output"];
  provider: Scalars["String"]["output"];
  readLimitation?: Maybe<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftAgentSecretBundleAttachment = {
  bundleId: Scalars["GUID"]["output"];
  bundleName: Scalars["String"]["output"];
  bundleSlug: Scalars["String"]["output"];
  environment: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  keyNames: Array<Scalars["String"]["output"]>;
  position: Scalars["Int"]["output"];
  prefix: Scalars["String"]["output"];
};

export type AstroliftAgentSecretBundleAttachmentMutationResult = {
  data?: Maybe<AstroliftAgentSecretBundleAttachment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentSecretBundleMutationResult = {
  data?: Maybe<AstroliftAgentSecretBundle>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentSecretReveal = {
  envVar: Scalars["String"]["output"];
  provider: Scalars["String"]["output"];
  revealedAt: Scalars["DateTime"]["output"];
  uri: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftAgentSecretRevealMutationResult = {
  data?: Maybe<AstroliftAgentSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentSecretStatus = {
  canReveal: Scalars["Boolean"]["output"];
  envVar: Scalars["String"]["output"];
  error?: Maybe<Scalars["String"]["output"]>;
  exists: Scalars["Boolean"]["output"];
  provider: Scalars["String"]["output"];
  readLimitation?: Maybe<Scalars["String"]["output"]>;
  uri: Scalars["String"]["output"];
};

export type AstroliftAgentSecretStatusFilter = {
  /** The store holds a value. */
  exists: InputMaybe<Scalars["Boolean"]["input"]>;
  /** true: the presence check reported an error. */
  failing: InputMaybe<Scalars["Boolean"]["input"]>;
  provider: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftAgentSecretStatusMutationResult = {
  data?: Maybe<AstroliftAgentSecretStatus>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentSecretStatusPage = {
  error?: Maybe<Scalars["String"]["output"]>;
  items: Array<AstroliftAgentSecretStatus>;
  page: Scalars["Int"]["output"];
  pageSize: Scalars["Int"]["output"];
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftAgentSkill = {
  position: Scalars["Int"]["output"];
  skill: AstroliftSkill;
  toolDefs: Array<AstroliftToolDef>;
};

export type AstroliftAgentStartupDiagnostic = {
  message: Scalars["String"]["output"];
  observedAt: Scalars["DateTime"]["output"];
  phase: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
};

export type AstroliftAgentTask = {
  agentName: Scalars["String"]["output"];
  agentSlug: Scalars["String"]["output"];
  callbackAttempts: Scalars["Int"]["output"];
  callbackLastError?: Maybe<Scalars["String"]["output"]>;
  callbackStatus?: Maybe<Scalars["String"]["output"]>;
  callbackUrl: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  dispatcher?: Maybe<AstroliftAgentTaskDispatcher>;
  eventSequence: Scalars["Int"]["output"];
  failureMessage?: Maybe<Scalars["String"]["output"]>;
  finishedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  namespace: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  projectSlug: Scalars["String"]["output"];
  provisioningAt?: Maybe<Scalars["DateTime"]["output"]>;
  queuedAt?: Maybe<Scalars["DateTime"]["output"]>;
  result?: Maybe<Scalars["JSON"]["output"]>;
  snapshotUrl?: Maybe<Scalars["String"]["output"]>;
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  startupDiagnostic?: Maybe<AstroliftAgentStartupDiagnostic>;
  status: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggeredByMe: Scalars["Boolean"]["output"];
  triggeredByUserId?: Maybe<Scalars["String"]["output"]>;
  updatedAt: Scalars["DateTime"]["output"];
  vncEnabled: Scalars["Boolean"]["output"];
  vncUrl: Scalars["String"]["output"];
};

export type AstroliftAgentTaskBacklog = {
  harness: Scalars["String"]["output"];
  items: Array<AstroliftAgentTaskBacklogItem>;
  revision: Scalars["Int"]["output"];
  sessionId: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftAgentTaskBacklogItem = {
  activeForm?: Maybe<Scalars["String"]["output"]>;
  details?: Maybe<Scalars["String"]["output"]>;
  id: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  text: Scalars["String"]["output"];
};

export type AstroliftAgentTaskDispatcher = {
  cloud: Scalars["String"]["output"];
  clusterId?: Maybe<Scalars["GUID"]["output"]>;
  clusterName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  region: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftAgentTaskEvent = {
  createdAt: Scalars["DateTime"]["output"];
  data?: Maybe<Scalars["JSON"]["output"]>;
  kind: Scalars["String"]["output"];
  messageId: Scalars["String"]["output"];
  request?: Maybe<Scalars["JSON"]["output"]>;
  sequence: Scalars["Int"]["output"];
  text: Scalars["String"]["output"];
  turnId: Scalars["String"]["output"];
};

export type AstroliftAgentTaskInputMessage = {
  author: Scalars["String"]["output"];
  clientRequestId?: Maybe<Scalars["String"]["output"]>;
  createdAt: Scalars["DateTime"]["output"];
  deliveredAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  message: Scalars["String"]["output"];
};

export type AstroliftAgentTaskInputMessageMutationResult = {
  data?: Maybe<AstroliftAgentTaskInputMessage>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentTaskInputReply = {
  authorLabel: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  requestSequence: Scalars["Int"]["output"];
  response: Scalars["JSON"]["output"];
};

export type AstroliftAgentTaskInputReplyMutationResult = {
  data?: Maybe<AstroliftAgentTaskInputReply>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentTaskLogLine = {
  container: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  level?: Maybe<Scalars["String"]["output"]>;
  message: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  stream: Scalars["String"]["output"];
  timestamp?: Maybe<Scalars["DateTime"]["output"]>;
};

export type AstroliftAgentTaskLogPage = {
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  hasMore: Scalars["Boolean"]["output"];
  items: Array<AstroliftAgentTaskLogLine>;
  liveOnly: Scalars["Boolean"]["output"];
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  pageSize: Scalars["Int"]["output"];
  windowLimited: Scalars["Boolean"]["output"];
};

export type AstroliftAgentTaskMutationResult = {
  data?: Maybe<AstroliftAgentTask>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAgentTaskPage = {
  items: Array<AstroliftAgentTask>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAgentTasksFilter = {
  /** Agent (workload) slugs. */
  agent: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Project slugs. */
  project: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Initiator user ids (as on the row), or "me". */
  startedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** true: runs the viewer started. false: runs someone or something else did. */
  startedByMe: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Task statuses, any of. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** manual, api, schedule, webhook, parent or unknown. */
  trigger: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftAgentTrigger = {
  branchPattern: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  enabled: Scalars["Boolean"]["output"];
  endpoint: Scalars["String"]["output"];
  inputMapping: Scalars["JSON"]["output"];
  lastTriggeredAt?: Maybe<Scalars["DateTime"]["output"]>;
  scmRepo: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAgentTriggerPage = {
  items: Array<AstroliftAgentTrigger>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAgentTriggerResult = {
  endpoint?: Maybe<Scalars["String"]["output"]>;
  message: Scalars["String"]["output"];
  ok: Scalars["Boolean"]["output"];
  signingSecret?: Maybe<Scalars["String"]["output"]>;
  slug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftAgentUpcomingRun = {
  agentId: Scalars["GUID"]["output"];
  agentName: Scalars["String"]["output"];
  agentSlug: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  cronExpression: Scalars["String"]["output"];
  projectSlug: Scalars["String"]["output"];
  scheduledAt: Scalars["DateTime"]["output"];
};

export type AstroliftAgentUpcomingRunPage = {
  items: Array<AstroliftAgentUpcomingRun>;
  page: Scalars["Int"]["output"];
  pageSize: Scalars["Int"]["output"];
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftAggregatedEvent = {
  count: Scalars["Int"]["output"];
  eventType: Scalars["String"]["output"];
  firstAt: Scalars["DateTime"]["output"];
  lastAt: Scalars["DateTime"]["output"];
  representative: AstroliftEvent;
  resourceId: Scalars["String"]["output"];
  resourceKind: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAggregatedEventPage = {
  items: Array<AstroliftAggregatedEvent>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAlertEvent = {
  acknowledgedAt?: Maybe<Scalars["DateTime"]["output"]>;
  detail: Scalars["JSON"]["output"];
  firedAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  resolvedAt?: Maybe<Scalars["DateTime"]["output"]>;
  ruleId: Scalars["GUID"]["output"];
  severity: Scalars["String"]["output"];
  summary: Scalars["String"]["output"];
};

export type AstroliftAlertEventMutationResult = {
  data?: Maybe<AstroliftAlertEvent>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAlertEventPage = {
  items: Array<AstroliftAlertEvent>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAlertEventSummary = {
  criticalCount: Scalars["Int"]["output"];
  unresolvedCount: Scalars["Int"]["output"];
};

export type AstroliftAlertMute = {
  createdBy: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  reason: Scalars["String"]["output"];
  ttlUntil: Scalars["DateTime"]["output"];
};

export type AstroliftAlertRule = {
  activeMute?: Maybe<AstroliftAlertMute>;
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  managedServiceId?: Maybe<Scalars["GUID"]["output"]>;
  name: Scalars["String"]["output"];
  notifyChannels: Scalars["JSON"]["output"];
  organizationSlug: Scalars["String"]["output"];
  predicate: Scalars["JSON"]["output"];
  severity: Scalars["String"]["output"];
  target: Scalars["String"]["output"];
  targetId: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftAlertRuleMutationResult = {
  data?: Maybe<AstroliftAlertRule>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAlertRulePage = {
  items: Array<AstroliftAlertRule>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAnonymizeUserInput = {
  userGid: Scalars["GUID"]["input"];
};

export type AstroliftAnonymizeUserPayload = {
  anonymizedAt: Scalars["DateTime"]["output"];
  anonymizedUserId: Scalars["GUID"]["output"];
  lifecycle: Scalars["String"]["output"];
  requiresLogout: Scalars["Boolean"]["output"];
  wasSelf: Scalars["Boolean"]["output"];
};

export type AstroliftAnonymizeUserPayloadMutationResult = {
  data?: Maybe<AstroliftAnonymizeUserPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftApiToken = {
  createdAt: Scalars["DateTime"]["output"];
  effectivePermissions: Array<Scalars["String"]["output"]>;
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  isRevoked: Scalars["Boolean"]["output"];
  lastUsedAgent?: Maybe<Scalars["String"]["output"]>;
  lastUsedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastUsedIp?: Maybe<Scalars["String"]["output"]>;
  name: Scalars["String"]["output"];
  scopes: Array<Scalars["String"]["output"]>;
  teamSlug?: Maybe<Scalars["String"]["output"]>;
  tokenLast4: Scalars["String"]["output"];
  user: AstroliftUser;
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftApiTokenPage = {
  items: Array<AstroliftApiToken>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftApiTokenPlaintext = {
  apiToken: AstroliftApiToken;
  plaintext: Scalars["String"]["output"];
};

export type AstroliftApiTokenPlaintextMutationResult = {
  data?: Maybe<AstroliftApiTokenPlaintext>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftApiTokenScope = {
  available: Scalars["Boolean"]["output"];
  description: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
  permissions: Array<Scalars["String"]["output"]>;
  sensitive: Scalars["Boolean"]["output"];
  surface: Scalars["String"]["output"];
  unavailableReason: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftApiTokenScopeCatalog = {
  presets: Array<AstroliftApiTokenScopePreset>;
  scopes: Array<AstroliftApiTokenScope>;
};

export type AstroliftApiTokenScopePreset = {
  key: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
  scopes: Array<Scalars["String"]["output"]>;
};

export type AstroliftAppAccess = {
  appSlug: Scalars["String"]["output"];
  enforcedOn: Array<Scalars["String"]["output"]>;
  groups: Array<Scalars["String"]["output"]>;
  managedByManifest: Scalars["Boolean"]["output"];
  restricted: Scalars["Boolean"]["output"];
  users: Array<Scalars["String"]["output"]>;
};

export type AstroliftAppAccessMutationResult = {
  data?: Maybe<AstroliftAppAccess>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAppAccessPreview = {
  allowed?: Maybe<Scalars["Int"]["output"]>;
  losing: Array<Scalars["String"]["output"]>;
  total?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAppAutowireStatus = {
  checkedAt?: Maybe<Scalars["DateTime"]["output"]>;
  ciWorkflow: Scalars["String"]["output"];
  connected: Scalars["Boolean"]["output"];
  detail: Scalars["String"]["output"];
  secrets: Scalars["String"]["output"];
  webhook: Scalars["String"]["output"];
};

export type AstroliftAppCertificate = {
  daysUntilExpiry: Scalars["Int"]["output"];
  hostname: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  issuer: Scalars["String"]["output"];
  notAfter: Scalars["String"]["output"];
  renewalStatus: Scalars["String"]["output"];
};

export type AstroliftAppCertificatesResult = {
  certificates: Array<AstroliftAppCertificate>;
  reason: AstroliftObservabilityPanelReason;
};

export type AstroliftAppConfigDrift = {
  environmentName: Scalars["String"]["output"];
  fields: Array<Scalars["String"]["output"]>;
  hasDrift: Scalars["Boolean"]["output"];
  lastChecked: Scalars["DateTime"]["output"];
};

export type AstroliftAppDeploymentSummary = {
  commitSha: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  imageTag: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  triggeredBy: Scalars["String"]["output"];
};

export type AstroliftAppDnsRecord = {
  name: Scalars["String"]["output"];
  propagationStatus: Scalars["String"]["output"];
  ttl: Scalars["Int"]["output"];
  type: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftAppDnsRecordsResult = {
  reason: AstroliftObservabilityPanelReason;
  records: Array<AstroliftAppDnsRecord>;
};

export type AstroliftAppDoctorCheck = {
  detail: Scalars["String"]["output"];
  fix: Scalars["String"]["output"];
  key: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftAppDoctorReport = {
  checks: Array<AstroliftAppDoctorCheck>;
  healthy: Scalars["Boolean"]["output"];
};

export type AstroliftAppDomain = {
  byoCertificateUploadedAt?: Maybe<Scalars["DateTime"]["output"]>;
  certExpiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  certIssuerSerial: Scalars["String"]["output"];
  certObservabilityStatus: Scalars["String"]["output"];
  certState: Scalars["String"]["output"];
  certificateState: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  edgeAuthState: Scalars["String"]["output"];
  expectedCnameTarget: Scalars["String"]["output"];
  hostname: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  isPlatformManagedZone: Scalars["Boolean"]["output"];
  isWildcard: Scalars["Boolean"]["output"];
  lastCertificateError: Scalars["String"]["output"];
  lastCheckedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastValidationError: Scalars["String"]["output"];
  pathRoutes: Array<AstroliftDomainPathRoute>;
  redirectRules: Array<AstroliftDomainRedirectRule>;
  registeredAppSlug: Scalars["String"]["output"];
  requiredDnsRecords: Array<AstroliftAppDomainRequiredRecord>;
  sniCertRef: Scalars["String"]["output"];
  txtChallengeToken: Scalars["String"]["output"];
  validationMethod: Scalars["String"]["output"];
  validationToken: Scalars["String"]["output"];
};

export type AstroliftAppDomainMutationResult = {
  data?: Maybe<AstroliftAppDomain>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAppDomainRequiredRecord = {
  kind: Scalars["String"]["output"];
  lastCheckedAt?: Maybe<Scalars["String"]["output"]>;
  message: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  propagated: Scalars["Boolean"]["output"];
  ttl: Scalars["Int"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftAppEndpointMetric = {
  errorRateRatio: Scalars["Float"]["output"];
  p50Ms?: Maybe<Scalars["Float"]["output"]>;
  p90Ms?: Maybe<Scalars["Float"]["output"]>;
  p99Ms?: Maybe<Scalars["Float"]["output"]>;
  requestRate: Scalars["Float"]["output"];
  route: Scalars["String"]["output"];
};

export type AstroliftAppEnvironment = {
  clusterId?: Maybe<Scalars["GUID"]["output"]>;
  clusterProviderPluginSlug?: Maybe<Scalars["String"]["output"]>;
  clusterSlug?: Maybe<Scalars["String"]["output"]>;
  createdAt: Scalars["DateTime"]["output"];
  deploysPaused: Scalars["Boolean"]["output"];
  domainZone?: Maybe<Scalars["String"]["output"]>;
  id: Scalars["GUID"]["output"];
  ingressPaused: Scalars["Boolean"]["output"];
  /** production, preview or other. */
  kind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  ownedByMe: Scalars["Boolean"]["output"];
  /** The owner's user pk: the environment's creator, else the app's. */
  ownerUserId?: Maybe<Scalars["String"]["output"]>;
  /** The bound cluster's region; empty when unset. */
  region: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  requiredApprovals: Scalars["Int"]["output"];
  settings: Array<AstroliftEnvironmentSetting>;
  url: Scalars["String"]["output"];
};

export type AstroliftAppEnvironmentMutationResult = {
  data?: Maybe<AstroliftAppEnvironment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAppEnvironmentPage = {
  items: Array<AstroliftAppEnvironment>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAppExecTarget = {
  appId: Scalars["GUID"]["output"];
  appVersion: Scalars["Int"]["output"];
  clusterId: Scalars["GUID"]["output"];
  clusterVersion: Scalars["Int"]["output"];
  container: Scalars["String"]["output"];
  environmentId: Scalars["GUID"]["output"];
  environmentName: Scalars["String"]["output"];
  environmentVersion: Scalars["Int"]["output"];
  namespace: Scalars["String"]["output"];
  podBinding: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  podUid: Scalars["String"]["output"];
  resumable: Scalars["Boolean"]["output"];
  workloadId: Scalars["GUID"]["output"];
  workloadVersion: Scalars["Int"]["output"];
};

export type AstroliftAppGoldenSignal = {
  name: GoldenSignalKind;
  promql: Scalars["String"]["output"];
  rangeSeconds: Scalars["Int"]["output"];
  reason: AstroliftObservabilityPanelReason;
  samples: Array<AstroliftTimeSeriesPoint>;
  unit: Scalars["String"]["output"];
};

export type AstroliftAppGoldenSignalsResult = {
  reason: AstroliftObservabilityPanelReason;
  signals: Array<AstroliftAppGoldenSignal>;
};

export type AstroliftAppHealthPulse = {
  ageSeconds?: Maybe<Scalars["Int"]["output"]>;
  message: Scalars["String"]["output"];
  status: AstroliftAppHealthPulseStatus;
};

export type AstroliftAppHealthPulseStatus = "DEGRADED" | "NEVER" | "OK" | "STALE";

export type AstroliftAppHealthSummary = {
  appName: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  environmentCount: Scalars["Int"]["output"];
  hasRecentFailure: Scalars["Boolean"]["output"];
  lastDeployedAt?: Maybe<Scalars["DateTime"]["output"]>;
  latestDeploymentStatus?: Maybe<Scalars["String"]["output"]>;
  latestImageTag: Scalars["String"]["output"];
  primitiveKind: Scalars["String"]["output"];
};

export type AstroliftAppIdentityBinding = {
  kind: Scalars["String"]["output"];
  lastUsedAt?: Maybe<Scalars["String"]["output"]>;
  roleArnOrPrincipal: Scalars["String"]["output"];
  trustPolicySummary: Scalars["String"]["output"];
};

export type AstroliftAppIdentityBindingResult = {
  binding?: Maybe<AstroliftAppIdentityBinding>;
  reason: AstroliftObservabilityPanelReason;
};

export type AstroliftAppListState =
  | "ARCHIVED"
  | "DEREGISTERED"
  | "FAILED"
  | "LIVE"
  | "PENDING"
  | "PROVISIONING"
  | "READY"
  | "TEARING_DOWN";

export type AstroliftAppListStatusFilter = "ALL" | "DEGRADED" | "NEVER_DEPLOYED" | "OK" | "STALE";

export type AstroliftAppLogExport = {
  byteCount: Scalars["Int"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  downloadUrl: Scalars["String"]["output"];
  errorMessage: Scalars["String"]["output"];
  expiresAt: Scalars["DateTime"]["output"];
  format: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  rowCount: Scalars["Int"]["output"];
  sha256: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  truncated: Scalars["Boolean"]["output"];
};

export type AstroliftAppLogExportMutationResult = {
  data?: Maybe<AstroliftAppLogExport>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAppLogLine = {
  container: Scalars["String"]["output"];
  message: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  stream: Scalars["String"]["output"];
  timestamp: Scalars["DateTime"]["output"];
};

export type AstroliftAppLogPage = {
  historicalAvailable: Scalars["Boolean"]["output"];
  items: Array<AstroliftAppLogQueryLine>;
  nextCursor: Scalars["String"]["output"];
  reachedRetention: Scalars["Boolean"]["output"];
  reason: AstroliftObservabilityPanelReason;
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftAppLogQueryLine = {
  container: Scalars["String"]["output"];
  level: Scalars["String"]["output"];
  message: Scalars["String"]["output"];
  podName: Scalars["String"]["output"];
  stream: Scalars["String"]["output"];
  timestamp: Scalars["String"]["output"];
};

export type AstroliftAppMetricNames = {
  error?: Maybe<Scalars["String"]["output"]>;
  limit: Scalars["Int"]["output"];
  names: Array<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  truncated: Scalars["Boolean"]["output"];
};

export type AstroliftAppMetrics = {
  appSlug: Scalars["String"]["output"];
  deployCount: Scalars["Int"]["output"];
  errorRate: Scalars["Float"]["output"];
  p50LatencyMs: Scalars["Float"]["output"];
  p95LatencyMs: Scalars["Float"]["output"];
  p99LatencyMs: Scalars["Float"]["output"];
  requestRate: Scalars["Float"]["output"];
  source: Scalars["String"]["output"];
  timeRange: Scalars["String"]["output"];
  timeSeries: Array<AstroliftAppMetricsPoint>;
};

export type AstroliftAppMetricsPoint = {
  errorRate: Scalars["Float"]["output"];
  latencyP95: Scalars["Float"]["output"];
  requestRate: Scalars["Float"]["output"];
  timestamp: Scalars["DateTime"]["output"];
};

export type AstroliftAppPod = {
  age?: Maybe<Scalars["DateTime"]["output"]>;
  containerStatuses: Array<AstroliftContainerStatus>;
  name: Scalars["String"]["output"];
  node: Scalars["String"]["output"];
  phase: Scalars["String"]["output"];
  ready: Scalars["Boolean"]["output"];
  recentErrorEvent?: Maybe<AstroliftAppPodEvent>;
  restarts: Scalars["Int"]["output"];
  status: Scalars["String"]["output"];
  workload: Scalars["String"]["output"];
};

export type AstroliftAppPodEvent = {
  count: Scalars["Int"]["output"];
  lastSeen: Scalars["String"]["output"];
  message: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
  type: Scalars["String"]["output"];
};

export type AstroliftAppReprovisionState = {
  elapsedSeconds?: Maybe<Scalars["Int"]["output"]>;
  needsReprovision: Scalars["Boolean"]["output"];
  reason: Scalars["String"]["output"];
  state: Scalars["String"]["output"];
};

export type AstroliftAppSecret = {
  bundleSlug: Scalars["String"]["output"];
  deploysAsShown: Scalars["Boolean"]["output"];
  environmentName: Scalars["String"]["output"];
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["String"]["output"];
  isMasked: Scalars["Boolean"]["output"];
  key: Scalars["String"]["output"];
  lastEditedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastEditedBy?: Maybe<AstroliftSecretEditor>;
  managedServiceKind: Scalars["String"]["output"];
  scope: Scalars["String"]["output"];
  setVia: Scalars["String"]["output"];
  source: Scalars["String"]["output"];
};

export type AstroliftAppSecretBundleAttachment = {
  attachedAt?: Maybe<Scalars["DateTime"]["output"]>;
  bundleName: Scalars["String"]["output"];
  bundleSlug: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  keyCount: Scalars["Int"]["output"];
  mergeOrder: Scalars["Int"]["output"];
  prefix: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  teamSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftAppSecretBundleAttachmentMutationResult = {
  data?: Maybe<AstroliftAppSecretBundleAttachment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAppSettingsLastModified = {
  deployStrategy?: Maybe<Scalars["DateTime"]["output"]>;
  deployTokens?: Maybe<Scalars["DateTime"]["output"]>;
  domains?: Maybe<Scalars["DateTime"]["output"]>;
  managedServices?: Maybe<Scalars["DateTime"]["output"]>;
  members?: Maybe<Scalars["DateTime"]["output"]>;
  observability?: Maybe<Scalars["DateTime"]["output"]>;
  secrets?: Maybe<Scalars["DateTime"]["output"]>;
  webhooks?: Maybe<Scalars["DateTime"]["output"]>;
};

export type AstroliftAppSourceKindFilter =
  | "ALL"
  | "BITBUCKET"
  | "GITEA"
  | "GITHUB"
  | "GITLAB"
  | "GIT_URL";

export type AstroliftAppSummary = {
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  primitiveKind: Scalars["String"]["output"];
  primitiveSlug: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftAppTeamAccess = {
  accessLevel: Scalars["String"]["output"];
  appId: Scalars["GUID"]["output"];
  appSlug: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  isHome: Scalars["Boolean"]["output"];
  teamId: Scalars["GUID"]["output"];
  teamName: Scalars["String"]["output"];
  teamSlug: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftAppTeamAccessMutationResult = {
  data?: Maybe<AstroliftAppTeamAccess>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftAppTeamAccessPage = {
  items: Array<AstroliftAppTeamAccess>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAppTrace = {
  durationMs: Scalars["Float"]["output"];
  rootOperation: Scalars["String"]["output"];
  rootService: Scalars["String"]["output"];
  spanCount: Scalars["Int"]["output"];
  statusCode: Scalars["String"]["output"];
  traceId: Scalars["String"]["output"];
};

export type AstroliftAppUptime = {
  isUp?: Maybe<Scalars["Boolean"]["output"]>;
  lastCheckedAt?: Maybe<Scalars["DateTime"]["output"]>;
  recent: Array<AstroliftAppUptimePoint>;
  totalChecks: Scalars["Int"]["output"];
  uptimePct: Scalars["Float"]["output"];
  windowHours: Scalars["Int"]["output"];
};

export type AstroliftAppUptimePoint = {
  checkedAt: Scalars["DateTime"]["output"];
  isUp: Scalars["Boolean"]["output"];
  latencyMs: Scalars["Int"]["output"];
  statusCode?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftAppUrlHealth = {
  lastChecked: Scalars["DateTime"]["output"];
  latencyMs?: Maybe<Scalars["Int"]["output"]>;
  message: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  statusCode?: Maybe<Scalars["Int"]["output"]>;
  url: Scalars["String"]["output"];
};

export type AstroliftApproverUser = {
  avatarUrl: Scalars["String"]["output"];
  displayName: Scalars["String"]["output"];
  email: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
};

export type AstroliftAppsListFilter = {
  /** true: archived apps only. false: active only. null: includeArchived decides. */
  archived: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Cluster slugs, case-insensitive; any environment on one matches. */
  cluster: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The last-deploy health pulse (the row's healthPulse.status). */
  deploy: InputMaybe<Array<AstroliftAppHealthPulseStatus>>;
  /** true: provisioning failed or the latest deploy failed. false: neither. */
  failing: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Topology kinds, as topologyKind. */
  kind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Project slug or name, case-insensitive. */
  project: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The header status. */
  status: InputMaybe<Array<AstroliftAppListState>>;
  /** Team slugs. */
  team: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftAssembleBriefResult = {
  briefId?: Maybe<Scalars["GUID"]["output"]>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAssembleBriefResultMutationResult = {
  data?: Maybe<AstroliftAssembleBriefResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAttestationChallengePayload = {
  challenge: Scalars["String"]["output"];
  expiresAt: Scalars["DateTime"]["output"];
  kind: Scalars["String"]["output"];
};

export type AstroliftAttestationChallengePayloadMutationResult = {
  data?: Maybe<AstroliftAttestationChallengePayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAttestationResult = {
  attestedAt?: Maybe<Scalars["DateTime"]["output"]>;
  kind: Scalars["String"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
  trustLevel: Scalars["String"]["output"];
};

export type AstroliftAttestationResultMutationResult = {
  data?: Maybe<AstroliftAttestationResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAuditEvent = {
  action: Scalars["String"]["output"];
  actorDisplay: Scalars["String"]["output"];
  actorId: Scalars["String"]["output"];
  actorKind: Scalars["String"]["output"];
  after?: Maybe<Scalars["JSON"]["output"]>;
  before?: Maybe<Scalars["JSON"]["output"]>;
  data: Scalars["JSON"]["output"];
  decision: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  organizationId?: Maybe<Scalars["String"]["output"]>;
  requestId: Scalars["String"]["output"];
  targetId: Scalars["String"]["output"];
  targetKind: Scalars["String"]["output"];
  targetSlug: Scalars["String"]["output"];
};

export type AstroliftAuditEventPage = {
  items: Array<AstroliftAuditEvent>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

/** The audit trail's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftAuditEventsFilter = {
  /** Exact actions, e.g. team.create. */
  action: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Actor ids (a user's pk as a string); "me" is the viewer. */
  actor: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** ALLOW, DENY or UNKNOWN. */
  decision: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** occurredAt >= since. */
  since: InputMaybe<Scalars["DateTime"]["input"]>;
  /** Events about one person: a user pk, or "me". Matches events targeting the user, any of the user's role bindings (granted or revoked), or naming the user in the event data (bulk revokes, team role assignments). */
  subjectUser: InputMaybe<Scalars["String"]["input"]>;
  /** The target's id, exact. */
  targetId: InputMaybe<Scalars["String"]["input"]>;
  /** Target kinds, case-insensitive, e.g. user, role_binding. */
  targetKind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** occurredAt <= until. */
  until: InputMaybe<Scalars["DateTime"]["input"]>;
};

export type AstroliftAuditExport = {
  byteCount: Scalars["Int"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  downloadUrl: Scalars["String"]["output"];
  expiresAt: Scalars["DateTime"]["output"];
  format: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  rowCount: Scalars["Int"]["output"];
  sha256: Scalars["String"]["output"];
};

export type AstroliftAuditExportMutationResult = {
  data?: Maybe<AstroliftAuditExport>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftAuditRetention = {
  days: Scalars["Int"]["output"];
};

export type AstroliftBrief = {
  config: Scalars["JSON"]["output"];
  contentHash: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  storageKey: Scalars["String"]["output"];
};

export type AstroliftBudget = {
  alertsAtPct: Array<Scalars["Int"]["output"]>;
  amountCents: Scalars["Int"]["output"];
  currency: Scalars["String"]["output"];
  currentSpendCents: Scalars["Int"]["output"];
  id: Scalars["GUID"]["output"];
  period: Scalars["String"]["output"];
  scopeId: Scalars["String"]["output"];
  scopeKind: Scalars["String"]["output"];
};

export type AstroliftBulkAssignTeamMemberRolesPayload = {
  alreadyAssignedCount: Scalars["Int"]["output"];
  assignedCount: Scalars["Int"]["output"];
  failedCount: Scalars["Int"]["output"];
  results: Array<AstroliftBulkOpItemResult>;
};

export type AstroliftBulkAssignTeamMemberRolesPayloadMutationResult = {
  data?: Maybe<AstroliftBulkAssignTeamMemberRolesPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftBulkDeploymentResultData = {
  failedCount: Scalars["Int"]["output"];
  results: Array<AstroliftBulkDeploymentResultItem>;
  succeededCount: Scalars["Int"]["output"];
};

export type AstroliftBulkDeploymentResultDataMutationResult = {
  data?: Maybe<AstroliftBulkDeploymentResultData>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftBulkDeploymentResultItem = {
  deployment?: Maybe<AstroliftDeployment>;
  deploymentId: Scalars["GUID"]["output"];
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftBulkOpItemResult = {
  alreadyExisted: Scalars["Boolean"]["output"];
  errors: Array<MutationError>;
  id: Scalars["GUID"]["output"];
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftBulkRevokeRoleBindingsPayload = {
  failedCount: Scalars["Int"]["output"];
  results: Array<AstroliftBulkOpItemResult>;
  revokedCount: Scalars["Int"]["output"];
};

export type AstroliftBulkRevokeRoleBindingsPayloadMutationResult = {
  data?: Maybe<AstroliftBulkRevokeRoleBindingsPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftCancelDeregisterPayload = {
  signalDelivered: Scalars["Boolean"]["output"];
  workflowId: Scalars["String"]["output"];
};

export type AstroliftCancelDeregisterPayloadMutationResult = {
  data?: Maybe<AstroliftCancelDeregisterPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftCapabilityDeprovisionPayload = {
  appId?: Maybe<Scalars["GUID"]["output"]>;
  clusterSlug: Scalars["String"]["output"];
  detail: Scalars["String"]["output"];
};

export type AstroliftCapabilityDeprovisionPayloadMutationResult = {
  data?: Maybe<AstroliftCapabilityDeprovisionPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftCiSecretValidation = {
  isCurrent?: Maybe<Scalars["Boolean"]["output"]>;
  isSet: Scalars["Boolean"]["output"];
  secretName: Scalars["String"]["output"];
  updatedAt: Scalars["String"]["output"];
};

export type AstroliftCiWorkflowResyncAllResult = {
  conflict: Scalars["Int"]["output"];
  failed: Scalars["Int"]["output"];
  inSync: Scalars["Int"]["output"];
  pushed: Scalars["Int"]["output"];
  repoDrift: Scalars["Int"]["output"];
  scanned: Scalars["Int"]["output"];
  skipped: Scalars["Int"]["output"];
  unknown: Scalars["Int"]["output"];
};

export type AstroliftCiWorkflowResyncAllResultMutationResult = {
  data?: Maybe<AstroliftCiWorkflowResyncAllResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftCiWorkflowSyncStatus = {
  checkedAt?: Maybe<Scalars["DateTime"]["output"]>;
  currentTemplateVersion: Scalars["Int"]["output"];
  detail: Scalars["String"]["output"];
  path: Scalars["String"]["output"];
  prUrl: Scalars["String"]["output"];
  renderedText: Scalars["String"]["output"];
  repoText: Scalars["String"]["output"];
  repoTextPulledAt?: Maybe<Scalars["DateTime"]["output"]>;
  state: Scalars["String"]["output"];
  syncedAt?: Maybe<Scalars["DateTime"]["output"]>;
  syncedTemplateVersion?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftCiWorkflowSyncStatusMutationResult = {
  data?: Maybe<AstroliftCiWorkflowSyncStatus>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftClusterAuthSource = {
  providerPluginId: Scalars["GUID"]["output"];
  providerPoolId: Scalars["String"]["output"];
  sourceVersion: Scalars["String"]["output"];
};

export type AstroliftClusterAuthUser = {
  createdAt?: Maybe<Scalars["DateTime"]["output"]>;
  email: Scalars["String"]["output"];
  enabled: Scalars["Boolean"]["output"];
  groups: Array<Scalars["String"]["output"]>;
  providerUserId?: Maybe<Scalars["String"]["output"]>;
  status: Scalars["String"]["output"];
  username: Scalars["String"]["output"];
};

export type AstroliftClusterAuthUserChange = {
  done: Scalars["Boolean"]["output"];
  username: Scalars["String"]["output"];
};

export type AstroliftClusterAuthUserChangeMutationResult = {
  data?: Maybe<AstroliftClusterAuthUserChange>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftClusterAuthUserMutationResult = {
  data?: Maybe<AstroliftClusterAuthUser>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftClusterAuthUsers = {
  groups: Array<Scalars["String"]["output"]>;
  provider: Scalars["String"]["output"];
  reachNote: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
  source?: Maybe<AstroliftClusterAuthSource>;
  supported: Scalars["Boolean"]["output"];
  users: Array<AstroliftClusterAuthUser>;
};

export type AstroliftClusterBootstrapComponent = {
  defaultEnabled: Scalars["Boolean"]["output"];
  helmValues: Scalars["JSON"]["output"];
  installedByRecipe: Scalars["Boolean"]["output"];
  key: Scalars["String"]["output"];
  options: Array<AstroliftClusterBootstrapOption>;
  rationale: Scalars["String"]["output"];
  requires: Array<Scalars["String"]["output"]>;
  runningOutsideRecipe: Scalars["Boolean"]["output"];
  title: Scalars["String"]["output"];
};

export type AstroliftClusterBootstrapOption = {
  choices: Array<AstroliftClusterBootstrapOptionChoice>;
  default: Scalars["String"]["output"];
  key: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
};

export type AstroliftClusterBootstrapOptionChoice = {
  label: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftClusterBootstrapPlan = {
  clusterId: Scalars["GUID"]["output"];
  components: Array<AstroliftClusterBootstrapComponent>;
  providerPluginSlug: Scalars["String"]["output"];
};

export type AstroliftClusterBootstrapRun = {
  chartVersion: Scalars["String"]["output"];
  cliVersion: Scalars["String"]["output"];
  endedAt: Scalars["DateTime"]["output"];
  errorMessage: Scalars["String"]["output"];
  hostInfo: Scalars["JSON"]["output"];
  id: Scalars["GUID"]["output"];
  installedReleases: Scalars["JSON"]["output"];
  startedAt: Scalars["DateTime"]["output"];
  status: Scalars["String"]["output"];
  triggeredByUsername?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftClusterCertificate = {
  arn: Scalars["String"]["output"];
  domainName: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftClusterCertificates = {
  certificates: Array<AstroliftClusterCertificate>;
  supported: Scalars["Boolean"]["output"];
};

export type AstroliftClusterEvent = {
  count: Scalars["Int"]["output"];
  firstSeen: Scalars["String"]["output"];
  involvedObject: Scalars["String"]["output"];
  lastSeen: Scalars["String"]["output"];
  message: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
  type: Scalars["String"]["output"];
};

export type AstroliftClusterHealth = {
  clusterId: Scalars["GUID"]["output"];
  events: Array<AstroliftClusterEvent>;
  pods: Array<AstroliftClusterPodPhase>;
};

export type AstroliftClusterLifecycleAuditEntry = {
  actor?: Maybe<Scalars["String"]["output"]>;
  errors: Array<Scalars["String"]["output"]>;
  operation: Scalars["String"]["output"];
  success: Scalars["Boolean"]["output"];
  timestamp: Scalars["DateTime"]["output"];
  variables: Scalars["JSON"]["output"];
};

export type AstroliftClusterLiveState = {
  agentProvisioned: Scalars["Boolean"]["output"];
  agentVersion: Scalars["String"]["output"];
  appReadiness: Scalars["JSON"]["output"];
  clusterId: Scalars["GUID"]["output"];
  cpuUtilization?: Maybe<Scalars["Float"]["output"]>;
  heartbeatAgeSeconds?: Maybe<Scalars["Float"]["output"]>;
  heartbeatIntervalSeconds: Scalars["Int"]["output"];
  ingressIps: Array<Scalars["String"]["output"]>;
  lastHeartbeatAt?: Maybe<Scalars["DateTime"]["output"]>;
  memoryUtilization?: Maybe<Scalars["Float"]["output"]>;
  nodeCount?: Maybe<Scalars["Int"]["output"]>;
  nodeReadyCount?: Maybe<Scalars["Int"]["output"]>;
  podTotal?: Maybe<Scalars["Int"]["output"]>;
  podsByNamespace: Scalars["JSON"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftClusterPodPhase = {
  count: Scalars["Int"]["output"];
  namespace: Scalars["String"]["output"];
  phase: Scalars["String"]["output"];
};

export type AstroliftClusterPrometheusMetrics = {
  available: Scalars["Boolean"]["output"];
  cpuUtilization?: Maybe<Scalars["Float"]["output"]>;
  deploymentReadyRatio?: Maybe<Scalars["Float"]["output"]>;
  memoryUtilization?: Maybe<Scalars["Float"]["output"]>;
  nodeCount?: Maybe<Scalars["Int"]["output"]>;
  podRunningRatio?: Maybe<Scalars["Float"]["output"]>;
  reason?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftClusterPrometheusRangeMetrics = {
  available: Scalars["Boolean"]["output"];
  rangeSeconds: Scalars["Int"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
  series: Array<AstroliftClusterPrometheusRangeSeries>;
  stepSeconds: Scalars["Int"]["output"];
};

export type AstroliftClusterPrometheusRangePoint = {
  ts: Scalars["Float"]["output"];
  value: Scalars["Float"]["output"];
};

export type AstroliftClusterPrometheusRangeSeries = {
  current?: Maybe<Scalars["Float"]["output"]>;
  label: Scalars["String"]["output"];
  metric: Scalars["String"]["output"];
  points: Array<AstroliftClusterPrometheusRangePoint>;
  unit: Scalars["String"]["output"];
};

export type AstroliftClusterSystemMetricPoint = {
  ts: Scalars["Float"]["output"];
  value: Scalars["Float"]["output"];
};

export type AstroliftClusterSystemMetricSeries = {
  current?: Maybe<Scalars["Float"]["output"]>;
  label: Scalars["String"]["output"];
  metric: Scalars["String"]["output"];
  points: Array<AstroliftClusterSystemMetricPoint>;
  unit: Scalars["String"]["output"];
};

export type AstroliftClusterSystemMetrics = {
  appNamespace: Scalars["String"]["output"];
  available: Scalars["Boolean"]["output"];
  rangeSeconds: Scalars["Int"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
  series: Array<AstroliftClusterSystemMetricSeries>;
  source: Scalars["String"]["output"];
  stepSeconds: Scalars["Int"]["output"];
};

export type AstroliftClusterWorkflowRun = {
  closedAt: Scalars["String"]["output"];
  runId: Scalars["String"]["output"];
  startedAt: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  workflowId: Scalars["String"]["output"];
  workflowType: Scalars["String"]["output"];
};

export type AstroliftClusterWorkloadHealth = {
  desiredReplicas: Scalars["Int"]["output"];
  lastImageDeployedAt: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
  readyReplicas: Scalars["Int"]["output"];
  restartCount24h: Scalars["Int"]["output"];
  workloadName: Scalars["String"]["output"];
};

export type AstroliftClustersListFilter = {
  /** Heartbeat statuses, as heartbeatStatus: never_seen, connected, degraded, offline. */
  live: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Provider plugin slugs, as providerPluginSlug. */
  provider: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Usernames of who registered the cluster; "me" is the viewer. */
  registeredBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Management lifecycles, as lifecycle (registered, managing, managed, ...). */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftCognitoUserPool = {
  domain: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  poolArn: Scalars["String"]["output"];
  poolId: Scalars["String"]["output"];
  region: Scalars["String"]["output"];
};

export type AstroliftCognitoUserPoolClient = {
  clientId: Scalars["String"]["output"];
  clientName: Scalars["String"]["output"];
};

export type AstroliftCommandRun = {
  command: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  exitCode?: Maybe<Scalars["Int"]["output"]>;
  id: Scalars["GUID"]["output"];
  invokedByMe: Scalars["Boolean"]["output"];
  invokedByUserId?: Maybe<Scalars["String"]["output"]>;
  invokedByUsername?: Maybe<Scalars["String"]["output"]>;
  logExcerpt: Scalars["String"]["output"];
  output: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  workloadSlug?: Maybe<Scalars["String"]["output"]>;
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftCommandRunPage = {
  items: Array<AstroliftCommandRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

/** The command runs list's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftCommandRunsFilter = {
  /** Who ran the command, as invokedByUserId; "me" is the viewer. */
  invokedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftConnectUserSourceProviderPayload = {
  authorizationUrl: Scalars["String"]["output"];
  providerConfigId: Scalars["GUID"]["output"];
};

export type AstroliftConnectUserSourceProviderPayloadMutationResult = {
  data?: Maybe<AstroliftConnectUserSourceProviderPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftContainer = {
  args: Array<Scalars["String"]["output"]>;
  buildContext: Scalars["String"]["output"];
  command: Array<Scalars["String"]["output"]>;
  dockerfilePath: Scalars["String"]["output"];
  env: Scalars["JSON"]["output"];
  healthcheckKind: Scalars["String"]["output"];
  healthcheckPort?: Maybe<Scalars["Int"]["output"]>;
  healthcheckValue: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  imageRef: Scalars["String"]["output"];
  isPrimary: Scalars["Boolean"]["output"];
  livenessProbe?: Maybe<Scalars["JSON"]["output"]>;
  name: Scalars["String"]["output"];
  port: Scalars["Int"]["output"];
  readinessProbe?: Maybe<Scalars["JSON"]["output"]>;
  startupProbe?: Maybe<Scalars["JSON"]["output"]>;
  workloadSlug: Scalars["String"]["output"];
};

export type AstroliftContainerResources = {
  cpuLimit: Scalars["String"]["output"];
  cpuRequest: Scalars["String"]["output"];
  memoryLimit: Scalars["String"]["output"];
  memoryRequest: Scalars["String"]["output"];
};

export type AstroliftContainerStatus = {
  image: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  lastRestartAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastRestartReasons: Array<Scalars["String"]["output"]>;
  name: Scalars["String"]["output"];
  ready: Scalars["Boolean"]["output"];
  resources: AstroliftContainerResources;
  restarts: Scalars["Int"]["output"];
  state: Scalars["String"]["output"];
  terminatedReason: Scalars["String"]["output"];
  waitingReason: Scalars["String"]["output"];
};

export type AstroliftCostAttribution = {
  attributedRows: Array<AstroliftCostBindingRow>;
  currency: Scalars["String"]["output"];
  totalCents: Scalars["Int"]["output"];
  unattributedCents: Scalars["Int"]["output"];
};

export type AstroliftCostBindingRow = {
  amountCents: Scalars["Int"]["output"];
  by: Scalars["String"]["output"];
  currency: Scalars["String"]["output"];
  managedServiceBindingId?: Maybe<Scalars["String"]["output"]>;
  managedServiceId?: Maybe<Scalars["String"]["output"]>;
  managedServiceKind?: Maybe<Scalars["String"]["output"]>;
  managedServiceName?: Maybe<Scalars["String"]["output"]>;
  registeredAppSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftCostForecast = {
  confidence: ForecastConfidence;
  currency: Scalars["String"]["output"];
  deltaPct: Scalars["Float"]["output"];
  mtdCents: Scalars["Int"]["output"];
  previousMonthCents: Scalars["Int"]["output"];
  projectedMonthlyCents: Scalars["Int"]["output"];
};

export type AstroliftCostSnapshot = {
  amountCents: Scalars["Int"]["output"];
  by: Scalars["String"]["output"];
  currency: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  managedServiceBindingId?: Maybe<Scalars["String"]["output"]>;
  projectId?: Maybe<Scalars["String"]["output"]>;
  registeredAppId?: Maybe<Scalars["String"]["output"]>;
  source: Scalars["String"]["output"];
  takenAt: Scalars["Date"]["output"];
};

export type AstroliftCostTrendPoint = {
  amountCents: Scalars["Int"]["output"];
  currency: Scalars["String"]["output"];
  date: Scalars["Date"]["output"];
  isAnomaly: Scalars["Boolean"]["output"];
};

export type AstroliftCronJobLastRun = {
  createdAt: Scalars["DateTime"]["output"];
  durationSeconds?: Maybe<Scalars["Int"]["output"]>;
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  exitCode?: Maybe<Scalars["Int"]["output"]>;
  id: Scalars["GUID"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
};

export type AstroliftDeelevatePayload = {
  previouslyElevated: Scalars["Boolean"]["output"];
};

export type AstroliftDeelevatePayloadMutationResult = {
  data?: Maybe<AstroliftDeelevatePayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftDeployToken = {
  createdAt: Scalars["DateTime"]["output"];
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  isRevoked: Scalars["Boolean"]["output"];
  last4: Scalars["String"]["output"];
  lastRotatedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastUsedAgent: Scalars["String"]["output"];
  lastUsedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastUsedIp: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  scopes: Array<Scalars["String"]["output"]>;
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftDeployTokenPage = {
  items: Array<AstroliftDeployToken>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftDeployTokenRotationMetadata = {
  rotationGraceSeconds: Scalars["Int"]["output"];
};

export type AstroliftDeployment = {
  abortedReason: Scalars["String"]["output"];
  approvalsReceived: Scalars["Int"]["output"];
  approvalsRequired: Scalars["Int"]["output"];
  approvedBy: Array<AstroliftDeploymentApprover>;
  awaitingApprovers: Array<AstroliftDeploymentApprover>;
  branch: Scalars["String"]["output"];
  buildError: Scalars["String"]["output"];
  ciActorKind: Scalars["String"]["output"];
  ciProvider: Scalars["String"]["output"];
  ciRunUrl: Scalars["String"]["output"];
  clusterRevision: Scalars["String"]["output"];
  commitAuthor: Scalars["String"]["output"];
  commitAuthorAvatarUrl: Scalars["String"]["output"];
  commitMessage: Scalars["String"]["output"];
  commitSha: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  durationSeconds?: Maybe<Scalars["Int"]["output"]>;
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentName: Scalars["String"]["output"];
  failedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  imageDigest: Scalars["String"]["output"];
  imageTag: Scalars["String"]["output"];
  manifestResyncError: Scalars["String"]["output"];
  manifestResyncStatus: Scalars["String"]["output"];
  phases: Array<AstroliftDeploymentPhase>;
  prNumber: Scalars["Int"]["output"];
  prUrl: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  repoUrl: Scalars["String"]["output"];
  requiredApproverCount: Scalars["Int"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  statusReason: Scalars["String"]["output"];
  strategy: Scalars["String"]["output"];
  succeededAt?: Maybe<Scalars["DateTime"]["output"]>;
  triggerKind: Scalars["String"]["output"];
  triggeredByMe: Scalars["Boolean"]["output"];
  triggeredByUserId?: Maybe<Scalars["String"]["output"]>;
  /** Version of this deployment snapshot for rollback and redeploy preconditions. */
  version: Scalars["Int"]["output"];
  workloadSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftDeploymentApprovalHistoryEntry = {
  action: Scalars["String"]["output"];
  actorDisplay: Scalars["String"]["output"];
  actorId: Scalars["String"]["output"];
  actorKind: Scalars["String"]["output"];
  decision: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  reason: Scalars["String"]["output"];
};

export type AstroliftDeploymentApprover = {
  approvedAt?: Maybe<Scalars["DateTime"]["output"]>;
  displayName: Scalars["String"]["output"];
  email: Scalars["String"]["output"];
  mailtoUrl: Scalars["String"]["output"];
  userId: Scalars["String"]["output"];
};

export type AstroliftDeploymentComparison = {
  baseSha: Scalars["String"]["output"];
  compareUrl: Scalars["String"]["output"];
  deploymentAId: Scalars["GUID"]["output"];
  deploymentBId: Scalars["GUID"]["output"];
  headSha: Scalars["String"]["output"];
  imageDiffSummary: Scalars["String"]["output"];
  manifestDiff: Array<AstroliftManifestDiffEntry>;
};

export type AstroliftDeploymentLifecycleEvent = {
  deploymentId: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  occurredAt: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftDeploymentLogEntry = {
  deploymentId: Scalars["String"]["output"];
  detail: Scalars["JSON"]["output"];
  event: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  message: Scalars["String"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  phase: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftDeploymentMetrics = {
  dailyFailed: Array<Scalars["Int"]["output"]>;
  dailyMeanDurationSeconds: Array<Maybe<Scalars["Float"]["output"]>>;
  dailySucceeded: Array<Scalars["Int"]["output"]>;
  failed: Scalars["Int"]["output"];
  inFlight: Scalars["Int"]["output"];
  meanDurationSeconds?: Maybe<Scalars["Float"]["output"]>;
  p95DurationSeconds?: Maybe<Scalars["Float"]["output"]>;
  rolledBack: Scalars["Int"]["output"];
  succeeded: Scalars["Int"]["output"];
  successRate: Scalars["Float"]["output"];
  total: Scalars["Int"]["output"];
  windowDays: Scalars["Int"]["output"];
};

export type AstroliftDeploymentMutationResult = {
  data?: Maybe<AstroliftDeployment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftDeploymentPage = {
  items: Array<AstroliftDeployment>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftDeploymentPhase = {
  completedAt?: Maybe<Scalars["DateTime"]["output"]>;
  failedAt?: Maybe<Scalars["DateTime"]["output"]>;
  healthyAt?: Maybe<Scalars["DateTime"]["output"]>;
  name: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
};

export type AstroliftDeploymentRunLogDownload = {
  content: Scalars["String"]["output"];
  contentType: Scalars["String"]["output"];
  filename: Scalars["String"]["output"];
};

export type AstroliftDeploymentRunLogPage = {
  hasMore: Scalars["Boolean"]["output"];
  items: Array<AstroliftDeploymentLogEntry>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  pageSize: Scalars["Int"]["output"];
};

/** The deployments list's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftDeploymentsFilter = {
  /** Started at or after, reading createdAt for a deploy not started yet. */
  startedAfter: InputMaybe<Scalars["DateTime"]["input"]>;
  /** Started at or before, reading createdAt for a deploy not started yet. */
  startedBefore: InputMaybe<Scalars["DateTime"]["input"]>;
  /** push, manual, ci, scheduled, rollback or promotion. */
  triggerKind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Who started the deploy, as triggeredByUserId; "me" is the viewer. */
  triggeredBy: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftDeregisterAppPayload = {
  stillLiveResources: Array<Scalars["String"]["output"]>;
  workflowId: Scalars["String"]["output"];
};

export type AstroliftDeregisterAppPayloadMutationResult = {
  data?: Maybe<AstroliftDeregisterAppPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftDeregisterPreview = {
  appName: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  deployTokens: Array<AstroliftDeregisterPreviewDeployToken>;
  identityRoles: Array<AstroliftDeregisterPreviewIdentityRole>;
  k8sObjects: Array<AstroliftDeregisterPreviewK8sObject>;
  managedServices: Array<AstroliftDeregisterPreviewManagedService>;
  registryRepoUri: Scalars["String"]["output"];
  secretRefs: Array<AstroliftDeregisterPreviewSecretRef>;
  sourceWebhook?: Maybe<AstroliftDeregisterPreviewSourceWebhook>;
  totalResourceCount: Scalars["Int"]["output"];
};

export type AstroliftDeregisterPreviewDeployToken = {
  environmentName?: Maybe<Scalars["String"]["output"]>;
  id: Scalars["GUID"]["output"];
  last4: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
};

export type AstroliftDeregisterPreviewIdentityRole = {
  clusterSlug: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  roleArnOrPrincipal: Scalars["String"]["output"];
};

export type AstroliftDeregisterPreviewK8sObject = {
  apiVersion: Scalars["String"]["output"];
  clusterSlug: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
};

export type AstroliftDeregisterPreviewManagedService = {
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  variant: Scalars["String"]["output"];
};

export type AstroliftDeregisterPreviewSecretRef = {
  bundleSlug: Scalars["String"]["output"];
  clusterSlug?: Maybe<Scalars["String"]["output"]>;
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  prefix: Scalars["String"]["output"];
};

export type AstroliftDeregisterPreviewSourceWebhook = {
  hookId: Scalars["String"]["output"];
  installed: Scalars["Boolean"]["output"];
  repo: Scalars["String"]["output"];
};

export type AstroliftDeviceRegistration = {
  driver: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  label: Scalars["String"]["output"];
  lastSeenAt?: Maybe<Scalars["DateTime"]["output"]>;
  platform: Scalars["String"]["output"];
  registeredAt: Scalars["DateTime"]["output"];
  tokenLast4: Scalars["String"]["output"];
};

export type AstroliftDeviceRegistrationMutationResult = {
  data?: Maybe<AstroliftDeviceRegistration>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftDisconnectUserSourceProviderPayload = {
  disconnectedId?: Maybe<Scalars["GUID"]["output"]>;
  providerConfigId: Scalars["GUID"]["output"];
};

export type AstroliftDisconnectUserSourceProviderPayloadMutationResult = {
  data?: Maybe<AstroliftDisconnectUserSourceProviderPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftDiscoveredAgentManifest = {
  alreadyRegistered: Scalars["Boolean"]["output"];
  manifestPath: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  workloadKind: Scalars["String"]["output"];
};

export type AstroliftDiscoveredAppManifest = {
  alreadyRegistered: Scalars["Boolean"]["output"];
  buildContext: Scalars["String"]["output"];
  manifestPath: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  workloadCount: Scalars["Int"]["output"];
};

export type AstroliftDispatcherInstance = {
  capabilities: Scalars["JSON"]["output"];
  id: Scalars["GUID"]["output"];
  lastHeartbeat?: Maybe<Scalars["DateTime"]["output"]>;
  registeredAt: Scalars["DateTime"]["output"];
  serviceUrl: Scalars["String"]["output"];
};

export type AstroliftDnsZone = {
  configJson: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  private: Scalars["Boolean"]["output"];
};

export type AstroliftDnsZones = {
  supported: Scalars["Boolean"]["output"];
  zones: Array<AstroliftDnsZone>;
};

export type AstroliftDomainPathRoute = {
  id: Scalars["GUID"]["output"];
  pathPrefix: Scalars["String"]["output"];
  priority: Scalars["Int"]["output"];
  stripPrefix: Scalars["Boolean"]["output"];
  targetPort: Scalars["Int"]["output"];
  targetWorkloadSlug: Scalars["String"]["output"];
};

export type AstroliftDomainRedirectRule = {
  destinationUrl: Scalars["String"]["output"];
  httpStatus: Scalars["Int"]["output"];
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  preserveQueryString: Scalars["Boolean"]["output"];
  priority: Scalars["Int"]["output"];
  sourcePattern: Scalars["String"]["output"];
};

export type AstroliftElevatePayload = {
  elevatedUntil: Scalars["DateTime"]["output"];
  method: Scalars["String"]["output"];
  secondsRemaining: Scalars["Int"]["output"];
};

export type AstroliftElevatePayloadMutationResult = {
  data?: Maybe<AstroliftElevatePayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftElevationStatus = {
  elevated: Scalars["Boolean"]["output"];
  elevatedUntil?: Maybe<Scalars["DateTime"]["output"]>;
  method?: Maybe<Scalars["String"]["output"]>;
  requiredFor: Array<Scalars["String"]["output"]>;
  secondsRemaining: Scalars["Int"]["output"];
};

export type AstroliftEmailAccountStatus = {
  bounceRatePct?: Maybe<Scalars["Float"]["output"]>;
  complaintRatePct?: Maybe<Scalars["Float"]["output"]>;
  productionAccess: Scalars["Boolean"]["output"];
  reputationScore?: Maybe<Scalars["Float"]["output"]>;
  sendingEnabled: Scalars["Boolean"]["output"];
};

export type AstroliftEmailDkimToken = {
  cnameHost: Scalars["String"]["output"];
  cnameTarget: Scalars["String"]["output"];
  token: Scalars["String"]["output"];
};

export type AstroliftEmailDnsAuthCheck = {
  message: Scalars["String"]["output"];
  outcome: Scalars["String"]["output"];
  protocol: Scalars["String"]["output"];
  records: Array<Scalars["String"]["output"]>;
};

export type AstroliftEmailDnsAuthStatus = {
  checkedAt: Scalars["DateTime"]["output"];
  dkim: AstroliftEmailDnsAuthCheck;
  dmarc: AstroliftEmailDnsAuthCheck;
  identity: Scalars["String"]["output"];
  overall: Scalars["String"]["output"];
  spf: AstroliftEmailDnsAuthCheck;
};

export type AstroliftEmailEngagementMetrics = {
  bounceRatePct: Scalars["Float"]["output"];
  clickRatePct: Scalars["Float"]["output"];
  complaintRatePct: Scalars["Float"]["output"];
  openRatePct: Scalars["Float"]["output"];
  totalBounces: Scalars["Int"]["output"];
  totalClicks: Scalars["Int"]["output"];
  totalComplaints: Scalars["Int"]["output"];
  totalDeliveries: Scalars["Int"]["output"];
  totalOpens: Scalars["Int"]["output"];
  totalSends: Scalars["Int"]["output"];
  windowDays: Scalars["Int"]["output"];
};

export type AstroliftEmailIdentityVerification = {
  dkimTokens: Array<AstroliftEmailDkimToken>;
  identity: Scalars["String"]["output"];
  isDomain: Scalars["Boolean"]["output"];
  status: Scalars["String"]["output"];
  verificationToken: Scalars["String"]["output"];
};

export type AstroliftEmailMessage = {
  eventKind: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  messageId: Scalars["String"]["output"];
  metadata: Scalars["JSON"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  recipient: Scalars["String"]["output"];
  subject: Scalars["String"]["output"];
};

export type AstroliftEmailSendQuota = {
  max24HourSend: Scalars["Float"]["output"];
  maxSendRate: Scalars["Float"]["output"];
  sentLast24h: Scalars["Float"]["output"];
};

export type AstroliftEmailServiceDetail = {
  accountStatus?: Maybe<AstroliftEmailAccountStatus>;
  dnsAuthStatus?: Maybe<AstroliftEmailDnsAuthStatus>;
  identity: Scalars["String"]["output"];
  identityVerification?: Maybe<AstroliftEmailIdentityVerification>;
  managedServiceId: Scalars["GUID"]["output"];
  pluginSlug: Scalars["String"]["output"];
  quota?: Maybe<AstroliftEmailSendQuota>;
  region: Scalars["String"]["output"];
  suppressionEntries: Array<AstroliftEmailSuppressionEntry>;
  unsupportedNotes: Array<Scalars["String"]["output"]>;
};

export type AstroliftEmailSuppressionEntry = {
  address: Scalars["String"]["output"];
  detail: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
  suppressedAt: Scalars["DateTime"]["output"];
};

export type AstroliftEmailTemplate = {
  createdAt?: Maybe<Scalars["DateTime"]["output"]>;
  htmlBody: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  subject: Scalars["String"]["output"];
  textBody: Scalars["String"]["output"];
};

export type AstroliftEmailTemplateMutationResult = {
  data?: Maybe<AstroliftEmailTemplate>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftEnrollmentQrPayload = {
  expiresAt: Scalars["DateTime"]["output"];
  qrPayload: Scalars["String"]["output"];
  qrSvg: Scalars["String"]["output"];
  sessionGuid: Scalars["GUID"]["output"];
  sessionId: Scalars["String"]["output"];
  verificationUri: Scalars["String"]["output"];
};

export type AstroliftEnrollmentQrPayloadMutationResult = {
  data?: Maybe<AstroliftEnrollmentQrPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftEnvironmentSetting = {
  id: Scalars["GUID"]["output"];
  key: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftEnvironmentSettingMutationResult = {
  data?: Maybe<AstroliftEnvironmentSetting>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** The environments list's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftEnvironmentsFilter = {
  /** App slugs. */
  app: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Cluster slugs, case-insensitive. */
  cluster: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** production, preview or other. */
  kind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The owner, as ownerUserId; "me" is the viewer. */
  owner: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The bound cluster's region, case-insensitive. */
  region: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftEvent = {
  eventType: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  organizationId?: Maybe<Scalars["String"]["output"]>;
  payload: Scalars["JSON"]["output"];
  projectId?: Maybe<Scalars["String"]["output"]>;
  registeredAppId?: Maybe<Scalars["String"]["output"]>;
  resourceId: Scalars["String"]["output"];
  resourceKind: Scalars["String"]["output"];
  severity: Scalars["String"]["output"];
  teamId?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftEventPage = {
  items: Array<AstroliftEvent>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  reason: AstroliftObservabilityPanelReason;
};

export type AstroliftExecutePromqlResult = {
  error: Scalars["String"]["output"];
  ok: Scalars["Boolean"]["output"];
  series: Array<AstroliftPromqlSeries>;
};

export type AstroliftForceRedeployPayload = {
  deploymentsCancelled: Scalars["Int"]["output"];
  dispatchMessage?: Maybe<Scalars["String"]["output"]>;
  k8sObjectsDeleted: Scalars["Int"]["output"];
  runUrl?: Maybe<Scalars["String"]["output"]>;
  workflowDispatched: Scalars["Boolean"]["output"];
};

export type AstroliftForceRedeployPayloadMutationResult = {
  data?: Maybe<AstroliftForceRedeployPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftForceRedeployPreview = {
  appSlug: Scalars["String"]["output"];
  environmentName?: Maybe<Scalars["String"]["output"]>;
  inFlightDeployments: Array<AstroliftForceRedeployPreviewDeployment>;
};

export type AstroliftForceRedeployPreviewDeployment = {
  ciActorKind: Scalars["String"]["output"];
  ciRunUrl: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  imageTag: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggeredByDisplay: Scalars["String"]["output"];
  workloadSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftFormDefinition = {
  createdAt: Scalars["DateTime"]["output"];
  createdByUsername?: Maybe<Scalars["String"]["output"]>;
  description: Scalars["String"]["output"];
  fieldConfig: Scalars["JSON"]["output"];
  formType: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isPublic: Scalars["Boolean"]["output"];
  logicRules: Scalars["JSON"]["output"];
  name: Scalars["String"]["output"];
  publishedAt?: Maybe<Scalars["DateTime"]["output"]>;
  schema: Scalars["JSON"]["output"];
  scoring: Scalars["JSON"]["output"];
  slug: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  submissionCount: Scalars["Int"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  updatedByUsername?: Maybe<Scalars["String"]["output"]>;
  version: Scalars["Int"]["output"];
};

export type AstroliftFormDefinitionMutationResult = {
  data?: Maybe<AstroliftFormDefinition>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftFormSubmission = {
  createdAt: Scalars["DateTime"]["output"];
  formName: Scalars["String"]["output"];
  formSlug: Scalars["String"]["output"];
  formVersion: Scalars["Int"]["output"];
  id: Scalars["GUID"]["output"];
  payload: Scalars["JSON"]["output"];
  sourceIp?: Maybe<Scalars["String"]["output"]>;
  status: Scalars["String"]["output"];
  submittedAt: Scalars["DateTime"]["output"];
  submitterDisplayName: Scalars["String"]["output"];
  submitterEmail: Scalars["String"]["output"];
  userAgent: Scalars["String"]["output"];
};

export type AstroliftFormSubmissionMutationResult = {
  data?: Maybe<AstroliftFormSubmission>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftGrantPreview = {
  action: Scalars["String"]["output"];
  /** Whether the caller's grant ceiling allows it. */
  allowed: Scalars["Boolean"]["output"];
  errors: Array<Scalars["String"]["output"]>;
  gaining: Array<AstroliftGrantPreviewPerson>;
  gainingCount: Scalars["Int"]["output"];
  groups: Array<AstroliftGrantPreviewGroup>;
  losing: Array<AstroliftGrantPreviewPerson>;
  losingCount: Scalars["Int"]["output"];
  notes: Array<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  /** The permissions in play. */
  permissions: Array<Scalars["String"]["output"]>;
  refusal?: Maybe<Scalars["String"]["output"]>;
  scopeGuid?: Maybe<Scalars["GUID"]["output"]>;
  scopeKind?: Maybe<Scalars["String"]["output"]>;
  sourceScopeLabel: Scalars["String"]["output"];
  summary: Scalars["String"]["output"];
  unchanged: Array<AstroliftGrantPreviewPerson>;
  unchangedCount: Scalars["Int"]["output"];
};

export type AstroliftGrantPreviewGroup = {
  groupExternalId: Scalars["String"]["output"];
  memberCount: Scalars["Int"]["output"];
};

export type AstroliftGrantPreviewInput = {
  /** GRANT, CHANGE or REMOVE. */
  action: Scalars["String"]["input"];
  /** The RoleBinding to change or remove. */
  bindingId: InputMaybe<Scalars["GUID"]["input"]>;
  expiresAt: InputMaybe<Scalars["DateTime"]["input"]>;
  principals: InputMaybe<Array<AstroliftPrincipalRef>>;
  roleId: InputMaybe<Scalars["GUID"]["input"]>;
  /** The scope's id. */
  scopeId: InputMaybe<Scalars["String"]["input"]>;
  /** ORG, TEAM, PROJECT, APP or AGENT. */
  scopeKind: InputMaybe<Scalars["String"]["input"]>;
};

export type AstroliftGrantPreviewPerson = {
  /** Permissions in play they would get. */
  gained: Array<Scalars["String"]["output"]>;
  /** Permissions in play they hold through another grant. */
  kept: Array<Scalars["String"]["output"]>;
  /** Permissions in play they would no longer hold. */
  lost: Array<Scalars["String"]["output"]>;
  memberId?: Maybe<Scalars["GUID"]["output"]>;
  /** How the change reaches them: "direct", "group <id>" or "team <slug>". */
  through: Array<Scalars["String"]["output"]>;
  user: AstroliftUser;
  /** Their other grants that carry permissions in play, excluding the one being changed. */
  via: Array<AstroliftGrantPreviewSource>;
};

export type AstroliftGrantPreviewSource = {
  bindingId?: Maybe<Scalars["GUID"]["output"]>;
  groupExternalId?: Maybe<Scalars["String"]["output"]>;
  /** Held on an ancestor of the scope, or through a share. */
  inherited: Scalars["Boolean"]["output"];
  /** The permissions in play it carries. */
  permissions: Array<Scalars["String"]["output"]>;
  roleName?: Maybe<Scalars["String"]["output"]>;
  roleSlug?: Maybe<Scalars["String"]["output"]>;
  scopeGuid?: Maybe<Scalars["GUID"]["output"]>;
  scopeKind?: Maybe<Scalars["String"]["output"]>;
  /** USER_BINDING, GROUP_BINDING, GROUP_MAPPING, TEAM_SHARE, or SUPERUSER. */
  source: Scalars["String"]["output"];
  sourceScopeLabel: Scalars["String"]["output"];
  teamSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftGroupRoleMapping = {
  createdAt: Scalars["DateTime"]["output"];
  groupExternalId: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  memberCount: Scalars["Int"]["output"];
  role: AstroliftRole;
  scopeGuid?: Maybe<Scalars["GUID"]["output"]>;
  scopeKind: Scalars["String"]["output"];
  sourceScopeLabel: Scalars["String"]["output"];
};

export type AstroliftGroupRoleMappingMutationResult = {
  data?: Maybe<AstroliftGroupRoleMapping>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftGroupRoleMappingPage = {
  items: Array<AstroliftGroupRoleMapping>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftHeartbeatSessionPayload = {
  id: Scalars["GUID"]["output"];
  lastSeenAt?: Maybe<Scalars["DateTime"]["output"]>;
};

export type AstroliftHeartbeatSessionPayloadMutationResult = {
  data?: Maybe<AstroliftHeartbeatSessionPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftIdentityCsvExport = {
  content: Scalars["String"]["output"];
  contentType: Scalars["String"]["output"];
  filename: Scalars["String"]["output"];
  rowCount: Scalars["Int"]["output"];
};

export type AstroliftIdentityProvider = {
  activatedAt?: Maybe<Scalars["DateTime"]["output"]>;
  clientId: Scalars["String"]["output"];
  config: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  isDefault: Scalars["Boolean"]["output"];
  kind: Scalars["String"]["output"];
  lastSwitchedByUsername?: Maybe<Scalars["String"]["output"]>;
  metadataUrl: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  oidcDiscoveryUrl: Scalars["String"]["output"];
  organizationSlug: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  version: Scalars["Int"]["output"];
};

export type AstroliftIdentityProviderMutationResult = {
  data?: Maybe<AstroliftIdentityProvider>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftImportSkillsResult = {
  importedSkills: Array<Scalars["String"]["output"]>;
  importedTools: Array<Scalars["String"]["output"]>;
  sourceRef: Scalars["String"]["output"];
};

export type AstroliftImportSkillsResultMutationResult = {
  data?: Maybe<AstroliftImportSkillsResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftInstallSourceWebhookPayload = {
  hookId: Scalars["String"]["output"];
  receiverUrl: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftInstallSourceWebhookPayloadMutationResult = {
  data?: Maybe<AstroliftInstallSourceWebhookPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftInvitation = {
  acceptedAt?: Maybe<Scalars["DateTime"]["output"]>;
  createdAt: Scalars["DateTime"]["output"];
  email: Scalars["String"]["output"];
  expiresAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  invitedByAvatarUrl?: Maybe<Scalars["String"]["output"]>;
  invitedByDisplayName?: Maybe<Scalars["String"]["output"]>;
  invitedByEmail?: Maybe<Scalars["String"]["output"]>;
  invitedByUserId?: Maybe<Scalars["String"]["output"]>;
  invitedByUsername?: Maybe<Scalars["String"]["output"]>;
  lastActiveAt?: Maybe<Scalars["DateTime"]["output"]>;
  roleSlug?: Maybe<Scalars["String"]["output"]>;
  scopeId: Scalars["String"]["output"];
  scopeKind: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftInvitationCreated = {
  acceptUrlPath: Scalars["String"]["output"];
  invitation: AstroliftInvitation;
  plaintextToken: Scalars["String"]["output"];
};

export type AstroliftInvitationCreatedMutationResult = {
  data?: Maybe<AstroliftInvitationCreated>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftInvitationMutationResult = {
  data?: Maybe<AstroliftInvitation>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftInvitationPage = {
  items: Array<AstroliftInvitation>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftInvitationsListFilter = {
  /** Usernames of who sent it; "me" is the viewer. */
  invitedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Slugs of the role the invitation grants. */
  role: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** pending, accepted, expired, revoked. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftJob = {
  containerImage: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  jobId: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  needs: Scalars["JSON"]["output"];
  pipelineId: Scalars["GUID"]["output"];
  runsOn: Scalars["String"]["output"];
};

export type AstroliftJobRun = {
  cleanupLastError?: Maybe<Scalars["String"]["output"]>;
  cleanupStatus: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  finishedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  job: AstroliftJob;
  logExcerpt: Scalars["String"]["output"];
  logKind: Scalars["String"]["output"];
  logTruncated: Scalars["Boolean"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  stepRuns: Array<AstroliftStepRun>;
  stepsTruncated: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftJobRunPage = {
  items: Array<AstroliftJobRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftLaunchTaskResult = {
  ok: Scalars["Boolean"]["output"];
  taskId?: Maybe<Scalars["GUID"]["output"]>;
};

export type AstroliftLaunchTaskResultMutationResult = {
  data?: Maybe<AstroliftLaunchTaskResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftListSortKey = "CREATED_ASC" | "CREATED_DESC" | "NAME_ASC" | "NAME_DESC";

export type AstroliftLogoutAllSessionsPayload = {
  keptCurrent: Scalars["Boolean"]["output"];
  revokedCount: Scalars["Int"]["output"];
};

export type AstroliftLogoutAllSessionsPayloadMutationResult = {
  data?: Maybe<AstroliftLogoutAllSessionsPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftManagedDomain = {
  challengeRecordName: Scalars["String"]["output"];
  challengeRecordValue: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  defaultFor: Scalars["String"]["output"];
  delegationCheck: Scalars["JSON"]["output"];
  dnsDriver: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isWildcardManaged: Scalars["Boolean"]["output"];
  organizationSlug?: Maybe<Scalars["String"]["output"]>;
  provisionClusterId?: Maybe<Scalars["String"]["output"]>;
  provisionNameservers: Scalars["JSON"]["output"];
  provisionState: Scalars["String"]["output"];
  provisionValidationRecords: Scalars["JSON"]["output"];
  verificationState: Scalars["String"]["output"];
  verifiedAt?: Maybe<Scalars["DateTime"]["output"]>;
  zone: Scalars["String"]["output"];
};

export type AstroliftManagedDomainMutationResult = {
  data?: Maybe<AstroliftManagedDomain>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftManagedService = {
  appliedConfig?: Maybe<Scalars["JSON"]["output"]>;
  attachments: Array<AstroliftManagedServiceAttachment>;
  bindingReady: Scalars["Boolean"]["output"];
  clusterSlug: Scalars["String"]["output"];
  config: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  deployedByEmail: Scalars["String"]["output"];
  deployedByMe: Scalars["Boolean"]["output"];
  editableFields: Array<Scalars["String"]["output"]>;
  environmentName: Scalars["String"]["output"];
  grantState: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isolation: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  lastActionAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastActionKind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  operationCompletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  operationKind: Scalars["String"]["output"];
  operationRunId: Scalars["String"]["output"];
  operationStartedAt?: Maybe<Scalars["DateTime"]["output"]>;
  operationWorkflowId: Scalars["String"]["output"];
  ownerScope: Scalars["String"]["output"];
  projectSlug: Scalars["String"]["output"];
  providerPortalUrl: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  statusError: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  variant: Scalars["String"]["output"];
  volumeBindings: Array<AstroliftManagedServiceVolumeBinding>;
  workloadIdentityGrants: Array<AstroliftWorkloadIdentityGrant>;
};

export type AstroliftManagedServiceAttachment = {
  consumerKind: Scalars["String"]["output"];
  consumerSlug: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
};

export type AstroliftManagedServiceAttachmentContext = {
  clusterId: Scalars["GUID"]["output"];
  consumerId: Scalars["GUID"]["output"];
  consumerKind: Scalars["String"]["output"];
  consumerSlug: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  environmentId?: Maybe<Scalars["GUID"]["output"]>;
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  managedServiceId: Scalars["GUID"]["output"];
  registeredAppId?: Maybe<Scalars["GUID"]["output"]>;
  version: Scalars["Int"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftManagedServiceAttachmentContextPage = {
  items: Array<AstroliftManagedServiceAttachmentContext>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftManagedServiceAttachmentMutationResult = {
  data?: Maybe<AstroliftManagedServiceAttachment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftManagedServiceCatalogEntry = {
  available: Scalars["Boolean"]["output"];
  bindingEnvs: Array<Scalars["String"]["output"]>;
  configSchema: Scalars["JSON"]["output"];
  description: Scalars["String"]["output"];
  displayName: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  isDefaultForKind: Scalars["Boolean"]["output"];
  issueUrl: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  providerPluginSlug: Scalars["String"]["output"];
  sizeOptions: Array<Scalars["String"]["output"]>;
  status: Scalars["String"]["output"];
  tier: Scalars["String"]["output"];
  unavailableReason: Scalars["String"]["output"];
  variant: Scalars["String"]["output"];
};

export type AstroliftManagedServiceConnection = {
  connectionSecretRef: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  keys: Array<AstroliftManagedServiceConnectionKey>;
  kind: Scalars["String"]["output"];
  managedServiceId: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  revealedAt: Scalars["DateTime"]["output"];
};

export type AstroliftManagedServiceConnectionKey = {
  isSecret: Scalars["Boolean"]["output"];
  key: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftManagedServiceConnectionMutationResult = {
  data?: Maybe<AstroliftManagedServiceConnection>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftManagedServiceContext = {
  clusterId: Scalars["GUID"]["output"];
  clusterSlug: Scalars["String"]["output"];
  clusterVersion: Scalars["Int"]["output"];
  contextRevision: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  environmentId?: Maybe<Scalars["GUID"]["output"]>;
  environmentName: Scalars["String"]["output"];
  environmentVersion?: Maybe<Scalars["Int"]["output"]>;
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  operationCompletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  operationKind: Scalars["String"]["output"];
  operationRunId: Scalars["String"]["output"];
  operationStartedAt?: Maybe<Scalars["DateTime"]["output"]>;
  operationWorkflowId: Scalars["String"]["output"];
  organizationId: Scalars["GUID"]["output"];
  ownerScope: Scalars["String"]["output"];
  projectId?: Maybe<Scalars["GUID"]["output"]>;
  projectSlug: Scalars["String"]["output"];
  registeredAppId?: Maybe<Scalars["GUID"]["output"]>;
  registeredAppSlug: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  variant: Scalars["String"]["output"];
  version: Scalars["Int"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftManagedServiceContextPage = {
  items: Array<AstroliftManagedServiceContext>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftManagedServiceCostPreview = {
  approximate: Scalars["Boolean"]["output"];
  available: Scalars["Boolean"]["output"];
  currency: Scalars["String"]["output"];
  lineItems: Scalars["JSON"]["output"];
  managedServiceId: Scalars["GUID"]["output"];
  message: Scalars["String"]["output"];
  monthlyTotal?: Maybe<Scalars["Float"]["output"]>;
  notes: Array<Scalars["String"]["output"]>;
  pricingFetchedAt: Scalars["String"]["output"];
  pricingSourceUrl: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
};

export type AstroliftManagedServiceMetricSeries = {
  name: Scalars["String"]["output"];
  samples: Array<AstroliftTimeSeriesPoint>;
  source: Scalars["String"]["output"];
  unit: Scalars["String"]["output"];
};

export type AstroliftManagedServiceMetrics = {
  kind: Scalars["String"]["output"];
  managedServiceId: Scalars["ID"]["output"];
  name: Scalars["String"]["output"];
  rangeSeconds: Scalars["Int"]["output"];
  series: Array<AstroliftManagedServiceMetricSeries>;
};

export type AstroliftManagedServiceMutationResult = {
  data?: Maybe<AstroliftManagedService>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftManagedServiceObject = {
  key: Scalars["String"]["output"];
  lastModified?: Maybe<Scalars["DateTime"]["output"]>;
  sizeBytes: Scalars["Int"]["output"];
};

export type AstroliftManagedServiceObjects = {
  cacheAgeSeconds?: Maybe<Scalars["Int"]["output"]>;
  kind: Scalars["String"]["output"];
  managedServiceId: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  objects: Array<AstroliftManagedServiceObject>;
  truncated: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftManagedServicePage = {
  items: Array<AstroliftManagedService>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftManagedServiceQueueDepth = {
  depth: Scalars["Int"]["output"];
  inFlight: Scalars["Int"]["output"];
  kind: Scalars["String"]["output"];
  managedServiceId: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  sampledAt?: Maybe<Scalars["DateTime"]["output"]>;
};

export type AstroliftManagedServiceTestEmailResult = {
  managedServiceId: Scalars["GUID"]["output"];
  recipient: Scalars["String"]["output"];
  sentAt: Scalars["DateTime"]["output"];
  subject: Scalars["String"]["output"];
  transport: Scalars["String"]["output"];
};

export type AstroliftManagedServiceTestEmailResultMutationResult = {
  data?: Maybe<AstroliftManagedServiceTestEmailResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftManagedServiceVolumeBinding = {
  accessModes: Array<Scalars["String"]["output"]>;
  capacity: Scalars["String"]["output"];
  claimName: Scalars["String"]["output"];
  claimNamespace: Scalars["String"]["output"];
  containerNames: Array<Scalars["String"]["output"]>;
  credentialReferenceCount: Scalars["Int"]["output"];
  csiDriver: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  mountPath: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  protocol: Scalars["String"]["output"];
  readOnly: Scalars["Boolean"]["output"];
  sourceKind: Scalars["String"]["output"];
  storageClassName: Scalars["String"]["output"];
  subPath: Scalars["String"]["output"];
  workloadNames: Array<Scalars["String"]["output"]>;
};

export type AstroliftManifestDiffEntry = {
  after: Scalars["JSON"]["output"];
  before: Scalars["JSON"]["output"];
  op: Scalars["String"]["output"];
  path: Scalars["String"]["output"];
};

export type AstroliftMarkOnboardingCompletePayload = {
  alreadyCompleted: Scalars["Boolean"]["output"];
  organization: AstroliftOrganization;
};

export type AstroliftMarkOnboardingCompletePayloadMutationResult = {
  data?: Maybe<AstroliftMarkOnboardingCompletePayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftMe = {
  id: Scalars["String"]["output"];
  modules: Array<AstroliftModuleEntitlement>;
  profile?: Maybe<AstroliftUserProfile>;
};

export type AstroliftMember = {
  createdAt: Scalars["DateTime"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  joinedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastActiveAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastSeenAt?: Maybe<Scalars["DateTime"]["output"]>;
  lifecycle: Scalars["String"]["output"];
  scopeId: Scalars["String"]["output"];
  scopeKind: Scalars["String"]["output"];
  /** The team a TEAM-scope row is on. */
  teamId?: Maybe<Scalars["GUID"]["output"]>;
  teamName?: Maybe<Scalars["String"]["output"]>;
  teamSlug?: Maybe<Scalars["String"]["output"]>;
  /** Every team in the organization the row's user is on. Set by astroliftMembersPage; null where the row is read elsewhere. */
  teams?: Maybe<Array<AstroliftMemberTeam>>;
  user: AstroliftUser;
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftMemberPage = {
  items: Array<AstroliftMember>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftMemberTeam = {
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftMembersListFilter = {
  /** Last active: 7d, 30d, 90d (within), stale (not in 90 days) or never. */
  active: InputMaybe<Scalars["String"]["input"]>;
  /** true: people holding, at organization scope, a role that can manage members. */
  admin: InputMaybe<Scalars["Boolean"]["input"]>;
  /** active, pending_invite, pending_first_login, suspended, deactivated. */
  lifecycle: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** true: people who share a team with the viewer. false: everyone else. */
  mine: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Role slugs the person holds on any scope of the organization. */
  role: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The row's own scope: ORG, TEAM, PROJECT, APP. ORG gives one row per person. */
  scopeKind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Team slugs or ids the person is on. */
  team: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftModelEndpointTest = {
  completionTokens?: Maybe<Scalars["Int"]["output"]>;
  error: Scalars["String"]["output"];
  latencyMs?: Maybe<Scalars["Int"]["output"]>;
  promptTokens?: Maybe<Scalars["Int"]["output"]>;
  reply: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  totalTokens?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftModelEndpointTestMutationResult = {
  data?: Maybe<AstroliftModelEndpointTest>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftModelEndpointsFilter = {
  /** Owning app slugs. */
  app: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Cluster slugs it runs on. */
  cluster: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** User ids, or "me". */
  deployedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** app or project. */
  ownerScope: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Project slugs: the owning project, or the owning app's project. */
  project: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Service statuses, any of. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Engines and providers: vllm, kserve, bedrock, azure_openai... */
  variant: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftModelPromptReadiness = {
  eligible: Scalars["Boolean"]["output"];
  maxOutputTokens: Scalars["Int"]["output"];
  maxPromptChars: Scalars["Int"]["output"];
  maxWaitSeconds: Scalars["Int"]["output"];
  promptsPerMinute: Scalars["Int"]["output"];
  state: AstroliftModelPromptReadinessState;
};

export type AstroliftModelPromptReadinessState =
  | "INACTIVE"
  | "READY"
  | "STALE_HEARTBEAT"
  | "UNAVAILABLE"
  | "UNCONFIGURED_MODEL"
  | "UNCONFIGURED_RELAY"
  | "UNKNOWN_HEARTBEAT"
  | "UNSUPPORTED";

export type AstroliftModuleEntitlement = {
  canCreate: Scalars["Boolean"]["output"];
  canManage: Scalars["Boolean"]["output"];
  canRun: Scalars["Boolean"]["output"];
  canView: Scalars["Boolean"]["output"];
  /** Whether the module is switched on for the active organization. Always true for apps, agents, workflows and admin. For the per-org modules (chat_studio_integration, agent_live_attach, chat_studio_agent_runs) it is true only when an org admin turned the module on and the install has not forced it off. Independent of the can* fields. */
  enabled: Scalars["Boolean"]["output"];
  key: Scalars["String"]["output"];
};

export type AstroliftMyConnectedAccount = {
  expiresAt?: Maybe<Scalars["String"]["output"]>;
  isConnected: Scalars["Boolean"]["output"];
  lastUsedAt?: Maybe<Scalars["String"]["output"]>;
  linkedAccountLogin?: Maybe<Scalars["String"]["output"]>;
  providerConfigId: Scalars["GUID"]["output"];
  providerKind: Scalars["String"]["output"];
  providerLabel: Scalars["String"]["output"];
  reauthRequired: Scalars["Boolean"]["output"];
};

export type AstroliftMyProfile = {
  email: Scalars["String"]["output"];
  firstName: Scalars["String"]["output"];
  lastName: Scalars["String"]["output"];
  lockedFields: Array<Scalars["String"]["output"]>;
  orgAllowsEdit: Scalars["Boolean"]["output"];
  timezone?: Maybe<Scalars["String"]["output"]>;
  userId: Scalars["Int"]["output"];
  username: Scalars["String"]["output"];
};

export type AstroliftMyProfileMutationResult = {
  data?: Maybe<AstroliftMyProfile>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftNavTree = {
  organization: AstroliftOrganization;
  teams: Array<AstroliftNavTreeTeam>;
  unassignedApps: Array<AstroliftAppSummary>;
};

export type AstroliftNavTreeProject = {
  apps: Array<AstroliftAppSummary>;
  project: AstroliftProject;
  standaloneAgents: Array<AstroliftAppSummary>;
  workflows: Array<AstroliftNavTreeWorkflow>;
};

export type AstroliftNavTreeTeam = {
  projects: Array<AstroliftNavTreeProject>;
  team: AstroliftTeam;
  unassignedApps: Array<AstroliftAppSummary>;
};

export type AstroliftNavTreeWorkflow = {
  agents: Array<AstroliftAppSummary>;
  childWorkflowIds: Array<Scalars["GUID"]["output"]>;
  id: Scalars["GUID"]["output"];
  isEnabled: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftNotification = {
  body: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  link: Scalars["String"]["output"];
  readAt?: Maybe<Scalars["DateTime"]["output"]>;
  title: Scalars["String"]["output"];
  userId: Scalars["String"]["output"];
};

export type AstroliftNotificationMutationResult = {
  data?: Maybe<AstroliftNotification>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftNotificationPreference = {
  channel: Scalars["String"]["output"];
  enabled: Scalars["Boolean"]["output"];
  eventKind: Scalars["String"]["output"];
  id?: Maybe<Scalars["GUID"]["output"]>;
};

export type AstroliftNotificationPreferenceMutationResult = {
  data?: Maybe<AstroliftNotificationPreference>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftNotificationProfile = {
  driver: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  retentionDeliveryDays: Scalars["Int"]["output"];
};

export type AstroliftNotificationProfileMutationResult = {
  data?: Maybe<AstroliftNotificationProfile>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftObservabilityPanelReason =
  | "ERROR"
  | "NOT_CONFIGURED"
  | "NOT_SUPPORTED_BY_PROVIDER"
  | "NO_DATA_YET"
  | "OK";

export type AstroliftOrgSkillRepo = {
  alias: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  defaultRef: Scalars["String"]["output"];
  displayName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  isPrivate: Scalars["Boolean"]["output"];
  repoFullName: Scalars["String"]["output"];
  sourceConnectionId?: Maybe<Scalars["GUID"]["output"]>;
  sourceKind: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftOrgSkillRepoMutationResult = {
  data?: Maybe<AstroliftOrgSkillRepo>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftOrganization = {
  allowUserProfileEdit: Scalars["Boolean"]["output"];
  appearanceDefault: Scalars["JSON"]["output"];
  appearanceLocked: Scalars["Boolean"]["output"];
  auditLogRetentionDays: Scalars["Int"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  defaultResourceTags: Scalars["JSON"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  logRetentionDaysDefault: Scalars["Int"]["output"];
  managedServiceIsolationPolicy: Scalars["JSON"]["output"];
  metricsRetentionDaysDefault: Scalars["Int"]["output"];
  metricsRollupRetentionDaysDefault: Scalars["Int"]["output"];
  name: Scalars["String"]["output"];
  onboardingCompletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  previewMaxActiveDefault: Scalars["Int"]["output"];
  restrictedSettingsDefault: Scalars["String"]["output"];
  scimEnabled: Scalars["Boolean"]["output"];
  slug: Scalars["String"]["output"];
  traceRetentionDaysDefault: Scalars["Int"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  website: Scalars["String"]["output"];
};

export type AstroliftOrganizationAllowlistedDomain = {
  createdAt: Scalars["DateTime"]["output"];
  defaultRoleSlug?: Maybe<Scalars["String"]["output"]>;
  domain: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  requiresReview: Scalars["Boolean"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftOrganizationAllowlistedDomainMutationResult = {
  data?: Maybe<AstroliftOrganizationAllowlistedDomain>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftOrganizationModule = {
  enabled: Scalars["Boolean"]["output"];
  key: Scalars["String"]["output"];
};

export type AstroliftOrganizationModuleMutationResult = {
  data?: Maybe<AstroliftOrganizationModule>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftOrganizationMutationResult = {
  data?: Maybe<AstroliftOrganization>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftPipeline = {
  astroliftApp?: Maybe<AstroliftRegisteredAppStub>;
  createdAt: Scalars["DateTime"]["output"];
  defaultBranch: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  organizationId?: Maybe<Scalars["GUID"]["output"]>;
  repoUrl: Scalars["String"]["output"];
  tomlPath: Scalars["String"]["output"];
  triggers: Array<AstroliftTrigger>;
  updatedAt: Scalars["DateTime"]["output"];
  version: Scalars["Int"]["output"];
};

export type AstroliftPipelineMutationResult = {
  data?: Maybe<AstroliftPipeline>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftPipelinePage = {
  items: Array<AstroliftPipeline>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftPipelineRun = {
  appId?: Maybe<Scalars["GUID"]["output"]>;
  cancellationLastError?: Maybe<Scalars["String"]["output"]>;
  cancellationObservedAt?: Maybe<Scalars["DateTime"]["output"]>;
  cancellationStatus: Scalars["String"]["output"];
  cleanupStatus: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  dispatchLastError?: Maybe<Scalars["String"]["output"]>;
  dispatchStatus: Scalars["String"]["output"];
  finishedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  jobRuns: Array<AstroliftJobRun>;
  jobsTruncated: Scalars["Boolean"]["output"];
  organizationId?: Maybe<Scalars["GUID"]["output"]>;
  pipelineId?: Maybe<Scalars["GUID"]["output"]>;
  pipelineVersion: Scalars["Int"]["output"];
  requestId?: Maybe<Scalars["String"]["output"]>;
  runNumber: Scalars["Int"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId: Scalars["String"]["output"];
  triggerActor: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggerRef: Scalars["String"]["output"];
  version: Scalars["Int"]["output"];
};

export type AstroliftPipelineRunMutationResult = {
  data?: Maybe<AstroliftPipelineRun>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftPipelineRunPage = {
  items: Array<AstroliftPipelineRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftPipelineSecret = {
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftPipelineSecretChange = {
  name: Scalars["String"]["output"];
  pipelineId: Scalars["GUID"]["output"];
};

export type AstroliftPipelineSecretChangeMutationResult = {
  data?: Maybe<AstroliftPipelineSecretChange>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftPodResourceUsage = {
  lastRestartAt?: Maybe<Scalars["DateTime"]["output"]>;
  podName: Scalars["String"]["output"];
  rangeSeconds: Scalars["Int"]["output"];
  reason: AstroliftObservabilityPanelReason;
  restartCount: Scalars["Int"]["output"];
  samples: Array<AstroliftPodResourceUsagePoint>;
};

export type AstroliftPodResourceUsagePoint = {
  cpuCores: Scalars["Float"]["output"];
  memoryBytes: Scalars["Float"]["output"];
  ts: Scalars["DateTime"]["output"];
};

export type AstroliftPoliciesListFilter = {
  /** Usernames of who created the policy; "me" is the viewer. */
  createdBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** ALLOW or DENY. */
  effect: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** ORG, TEAM, PROJECT, APP. */
  scopeLevel: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftPolicy = {
  actionPattern: Scalars["String"]["output"];
  actorPattern: Scalars["JSON"]["output"];
  conditions: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  createdByUsername?: Maybe<Scalars["String"]["output"]>;
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  description: Scalars["String"]["output"];
  effect: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  resourcePattern: Scalars["JSON"]["output"];
  scopeId?: Maybe<Scalars["String"]["output"]>;
  scopeLevel: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  updatedByUsername?: Maybe<Scalars["String"]["output"]>;
  version: Scalars["Int"]["output"];
};

export type AstroliftPolicyConditionCatalog = {
  actorKeys: Array<AstroliftPolicyPatternKey>;
  conditions: Array<AstroliftPolicyConditionKind>;
  effects: Array<Scalars["String"]["output"]>;
  resourceKeys: Array<AstroliftPolicyPatternKey>;
  scopeLevels: Array<Scalars["String"]["output"]>;
};

export type AstroliftPolicyConditionField = {
  default?: Maybe<Scalars["JSON"]["output"]>;
  description: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
  minimum?: Maybe<Scalars["Int"]["output"]>;
  name: Scalars["String"]["output"];
  options: Array<Scalars["String"]["output"]>;
  required: Scalars["Boolean"]["output"];
  /** weekdays, time_ranges, time_zone, cidrs, strings or integer. */
  type: Scalars["String"]["output"];
};

export type AstroliftPolicyConditionKind = {
  description: Scalars["String"]["output"];
  example: Scalars["JSON"]["output"];
  fields: Array<AstroliftPolicyConditionField>;
  kind: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
  /** What a check must carry to evaluate it: clock, client_ip, session (the request's own user only) or operation (supplied by the call site). */
  needs: Scalars["String"]["output"];
};

export type AstroliftPolicyDraftInput = {
  actionPattern: Scalars["String"]["input"];
  actorPattern: InputMaybe<Scalars["JSON"]["input"]>;
  conditions: InputMaybe<Scalars["JSON"]["input"]>;
  effect: Scalars["String"]["input"];
  resourcePattern: InputMaybe<Scalars["JSON"]["input"]>;
  /** The scope's id, or its numeric id. */
  scopeId: InputMaybe<Scalars["String"]["input"]>;
  scopeLevel: Scalars["String"]["input"];
};

export type AstroliftPolicyMutationResult = {
  data?: Maybe<AstroliftPolicy>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftPolicyPage = {
  items: Array<AstroliftPolicy>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftPolicyPatternKey = {
  description: Scalars["String"]["output"];
  key: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
};

export type AstroliftPolicySimulation = {
  /** Catalog permissions the action pattern matches. */
  actions: Array<Scalars["String"]["output"]>;
  /** Whether the org has recorded decisions in the window. */
  auditRecorded: Scalars["Boolean"]["output"];
  decisions: Array<AstroliftPolicySimulationDecision>;
  decisionsDeniedCount: Scalars["Int"]["output"];
  decisionsEvaluated: Scalars["Int"]["output"];
  decisionsUnknownCount: Scalars["Int"]["output"];
  errors: Array<Scalars["String"]["output"]>;
  holders: Array<AstroliftPolicySimulationHolder>;
  holdersCount: Scalars["Int"]["output"];
  holdersDeniedCount: Scalars["Int"]["output"];
  holdersUnknownCount: Scalars["Int"]["output"];
  notes: Array<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  sources: Array<Scalars["String"]["output"]>;
  windowDays: Scalars["Int"]["output"];
};

export type AstroliftPolicySimulationDecision = {
  action: Scalars["String"]["output"];
  actorDisplay: Scalars["String"]["output"];
  actorId: Scalars["String"]["output"];
  detail: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  occurredAt: Scalars["DateTime"]["output"];
  /** DENIED or UNKNOWN. */
  outcome: Scalars["String"]["output"];
  permissions: Array<Scalars["String"]["output"]>;
};

export type AstroliftPolicySimulationHolder = {
  /** Actions it would not deny. */
  allowed: Array<Scalars["String"]["output"]>;
  /** Actions it would definitely deny them. */
  denied: Array<Scalars["String"]["output"]>;
  detail: Scalars["String"]["output"];
  memberId?: Maybe<Scalars["GUID"]["output"]>;
  /** DENIED, UNKNOWN (fail-closed guess) or NOT_DENIED. */
  outcome: Scalars["String"]["output"];
  scopeGuid?: Maybe<Scalars["GUID"]["output"]>;
  scopeKind: Scalars["String"]["output"];
  sourceScopeLabel: Scalars["String"]["output"];
  /** Actions it would deny for want of an attribute a simulation cannot know. */
  unknown: Array<Scalars["String"]["output"]>;
  user: AstroliftUser;
};

export type AstroliftPreviewAggregateResources = {
  cpuCores: Scalars["Float"]["output"];
  memoryBytes: Scalars["Float"]["output"];
  podCount: Scalars["Int"]["output"];
};

export type AstroliftPreviewDeployment = {
  appId: Scalars["GUID"]["output"];
  commitSha: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentId: Scalars["GUID"]["output"];
  id: Scalars["GUID"]["output"];
  imageTag: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  version: Scalars["Int"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftPreviewDeploymentPage = {
  items: Array<AstroliftPreviewDeployment>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftPreviewEnvironment = {
  aggregateResources: AstroliftPreviewAggregateResources;
  branch: Scalars["String"]["output"];
  commitSha: Scalars["String"]["output"];
  environment?: Maybe<AstroliftPreviewEnvironmentTarget>;
  /** available, retired or unavailable; retired targets cannot route logs. */
  environmentStatus: Scalars["String"]["output"];
  estimatedCostApproximate: Scalars["Boolean"]["output"];
  estimatedCostNotes: Array<Scalars["String"]["output"]>;
  estimatedDailyCostUsd?: Maybe<Scalars["Float"]["output"]>;
  /** Why a failed preview failed, in one line: the build's recorded reason, else its latest deployment's. Empty unless status is failed. */
  failureReason: Scalars["String"]["output"];
  hostname: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isManual: Scalars["Boolean"]["output"];
  isPinned: Scalars["Boolean"]["output"];
  lastDeployedAt?: Maybe<Scalars["DateTime"]["output"]>;
  namespace: Scalars["String"]["output"];
  openedByLogin: Scalars["String"]["output"];
  openedByMe: Scalars["Boolean"]["output"];
  openedByUserId?: Maybe<Scalars["String"]["output"]>;
  pinReason: Scalars["String"]["output"];
  pinnedAt?: Maybe<Scalars["DateTime"]["output"]>;
  pinnedByEmail?: Maybe<Scalars["String"]["output"]>;
  prNumber: Scalars["Int"]["output"];
  prUrl: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  /** not_requested, available or unavailable; basic reads do not query pods or pricing. */
  runtimeStatus: Scalars["String"]["output"];
  sourceUrl: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  tornDownAt?: Maybe<Scalars["DateTime"]["output"]>;
  ttlUntil: Scalars["DateTime"]["output"];
  /** Version of this exact preview binding. */
  version: Scalars["Int"]["output"];
};

export type AstroliftPreviewEnvironmentCounts = {
  building: Scalars["Int"]["output"];
  failed: Scalars["Int"]["output"];
  running: Scalars["Int"]["output"];
  tornDown: Scalars["Int"]["output"];
  total: Scalars["Int"]["output"];
};

export type AstroliftPreviewEnvironmentMutationResult = {
  data?: Maybe<AstroliftPreviewEnvironment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftPreviewEnvironmentPage = {
  items: Array<AstroliftPreviewEnvironment>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftPreviewEnvironmentTarget = {
  appId: Scalars["GUID"]["output"];
  appSlug: Scalars["String"]["output"];
  appVersion: Scalars["Int"]["output"];
  clusterId: Scalars["GUID"]["output"];
  clusterVersion: Scalars["Int"]["output"];
  environmentId: Scalars["GUID"]["output"];
  environmentName: Scalars["String"]["output"];
  environmentVersion: Scalars["Int"]["output"];
  namespace: Scalars["String"]["output"];
  previewId: Scalars["GUID"]["output"];
  previewVersion: Scalars["Int"]["output"];
};

/** The previews list's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftPreviewEnvironmentsFilter = {
  /** true: branch previews started by hand. false: pull request previews. */
  manual: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Who opened it: an SCM or platform login, case-insensitive; "me" is the viewer. */
  openedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** building, running, failed or torn_down. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftPrincipal = {
  avatarUrl?: Maybe<Scalars["String"]["output"]>;
  /** A group's role bindings here. */
  bindingsCount?: Maybe<Scalars["Int"]["output"]>;
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  groupExternalId?: Maybe<Scalars["String"]["output"]>;
  invitationId?: Maybe<Scalars["GUID"]["output"]>;
  invitationStatus?: Maybe<Scalars["String"]["output"]>;
  /** A stable id for the row: user:<userId>, group:<externalId>, team:<teamId> or invitation:<invitationId>. */
  key: Scalars["String"]["output"];
  /** USER, GROUP, TEAM or INVITATION. */
  kind: Scalars["String"]["output"];
  lifecycle?: Maybe<Scalars["String"]["output"]>;
  /** A group's role mappings here. */
  mappingsCount?: Maybe<Scalars["Int"]["output"]>;
  /** Members of a group (active, carrying it) or of a team. */
  memberCount?: Maybe<Scalars["Int"]["output"]>;
  /** The user's ORG membership row. */
  memberId?: Maybe<Scalars["GUID"]["output"]>;
  /** What to show: display name, group id, team or email. */
  name: Scalars["String"]["output"];
  /** Email for a user, slug for a team, else empty. */
  secondary: Scalars["String"]["output"];
  teamId?: Maybe<Scalars["GUID"]["output"]>;
  teamSlug?: Maybe<Scalars["String"]["output"]>;
  user?: Maybe<AstroliftUser>;
  /** The value grantRole takes as userId. */
  userId?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftPrincipalKindCount = {
  count: Scalars["Int"]["output"];
  kind: Scalars["String"]["output"];
};

export type AstroliftPrincipalPage = {
  /** Matches per kind searched, for the view tabs. */
  counts: Array<AstroliftPrincipalKindCount>;
  items: Array<AstroliftPrincipal>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  page: Scalars["Int"]["output"];
  pageSize: Scalars["Int"]["output"];
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftPrincipalRef = {
  /** USER: the user id grantRole takes. GROUP: the external id. TEAM: the team id. */
  id: Scalars["String"]["input"];
  /** USER, GROUP or TEAM. */
  kind: Scalars["String"]["input"];
};

export type AstroliftPrincipalSearchFilter = {
  /** USER, GROUP, TEAM, INVITATION. Unset searches every kind. */
  kind: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftProject = {
  createdAt: Scalars["DateTime"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  organization: AstroliftOrganization;
  slug: Scalars["String"]["output"];
  team: AstroliftTeam;
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftProjectMutationResult = {
  data?: Maybe<AstroliftProject>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftProjectPage = {
  items: Array<AstroliftProject>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftPromqlSeries = {
  metricLabels: Scalars["JSON"]["output"];
  values: Array<AstroliftTimeSeriesPoint>;
};

export type AstroliftProviderPlugin = {
  capabilitiesManifest: Scalars["JSON"]["output"];
  id: Scalars["GUID"]["output"];
  isEnabled: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  version: Scalars["String"]["output"];
};

export type AstroliftProviderRegion = {
  continent: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
};

export type AstroliftProvisioningProgress = {
  completed: Array<Scalars["String"]["output"]>;
  currentStep: Scalars["String"]["output"];
  totalSteps: Array<Scalars["String"]["output"]>;
};

export type AstroliftPushCiSecretsPayload = {
  repo: Scalars["String"]["output"];
  rotatedTokenLast4: Scalars["String"]["output"];
  secretNames: Array<Scalars["String"]["output"]>;
};

export type AstroliftPushCiSecretsPayloadMutationResult = {
  data?: Maybe<AstroliftPushCiSecretsPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftPushCiWorkflowPayload = {
  commitSha?: Maybe<Scalars["String"]["output"]>;
  prUrl?: Maybe<Scalars["String"]["output"]>;
  status: Scalars["String"]["output"];
};

export type AstroliftPushCiWorkflowPayloadMutationResult = {
  data?: Maybe<AstroliftPushCiWorkflowPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftQuota = {
  currentUsage: Scalars["Float"]["output"];
  hardLimit: Scalars["Float"]["output"];
  id: Scalars["GUID"]["output"];
  pendingRequest?: Maybe<AstroliftQuotaIncreaseRequest>;
  resource: Scalars["String"]["output"];
  scopeId: Scalars["String"]["output"];
  scopeKind: Scalars["String"]["output"];
  softLimit: Scalars["Float"]["output"];
};

export type AstroliftQuotaIncreaseRequest = {
  createdAt: Scalars["DateTime"]["output"];
  decidedAt?: Maybe<Scalars["DateTime"]["output"]>;
  decidedByDisplay?: Maybe<Scalars["String"]["output"]>;
  decisionNote: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  quotaId: Scalars["GUID"]["output"];
  reason: Scalars["String"]["output"];
  requestedByDisplay: Scalars["String"]["output"];
  requestedFactor: Scalars["Float"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftQuotaIncreaseRequestMutationResult = {
  data?: Maybe<AstroliftQuotaIncreaseRequest>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftQuotaUsagePoint = {
  date: Scalars["Date"]["output"];
  limit: Scalars["Float"]["output"];
  used: Scalars["Float"]["output"];
};

export type AstroliftRegisterAgentRepoResult = {
  agents: Array<AstroliftRegisteredAgent>;
  workflows: Array<Scalars["String"]["output"]>;
};

export type AstroliftRegisterAgentRepoResultMutationResult = {
  data?: Maybe<AstroliftRegisterAgentRepoResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRegisterAppRepoResult = {
  apps: Array<AstroliftRegisteredAppEntry>;
};

export type AstroliftRegisterAppRepoResultMutationResult = {
  data?: Maybe<AstroliftRegisterAppRepoResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRegisteredAgent = {
  appId: Scalars["GUID"]["output"];
  created: Scalars["Boolean"]["output"];
  manifestPath: Scalars["String"]["output"];
  skillNotes: Array<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
  workloadSlug: Scalars["String"]["output"];
};

export type AstroliftRegisteredApp = {
  activePreviewCount: Scalars["Int"]["output"];
  approverTeamId?: Maybe<Scalars["GUID"]["output"]>;
  approverUserIds: Array<Scalars["String"]["output"]>;
  archivedAt?: Maybe<Scalars["DateTime"]["output"]>;
  archivedByEmail?: Maybe<Scalars["String"]["output"]>;
  autowire?: Maybe<AstroliftAppAutowireStatus>;
  buildArgs: Scalars["JSON"]["output"];
  buildContext: Scalars["String"]["output"];
  buildMode: Scalars["String"]["output"];
  buildStrategy: Scalars["String"]["output"];
  ciWorkflowSyncStatus?: Maybe<AstroliftCiWorkflowSyncStatus>;
  /** Clusters the app's environments deploy to, in environment-name order. Filled on astroliftAppsPage and astroliftMyAppsPage (#2149). */
  clusterSlugs: Array<Scalars["String"]["output"]>;
  configDrift?: Maybe<AstroliftAppConfigDrift>;
  createdAt: Scalars["DateTime"]["output"];
  cronExpression: Scalars["String"]["output"];
  cronPaused: Scalars["Boolean"]["output"];
  defaultBranch: Scalars["String"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  deployBranch: Scalars["String"]["output"];
  deployTokenLast4: Scalars["String"]["output"];
  description: Scalars["String"]["output"];
  dockerfilePath: Scalars["String"]["output"];
  ecrPushRoleArn: Scalars["String"]["output"];
  ecrRepoUri: Scalars["String"]["output"];
  healthPulse?: Maybe<AstroliftAppHealthPulse>;
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  isArchived: Scalars["Boolean"]["output"];
  k8sNamespace: Scalars["String"]["output"];
  lastDeployedAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastResyncAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastSyncedHash: Scalars["String"]["output"];
  latestDeployment?: Maybe<AstroliftAppDeploymentSummary>;
  logRetentionDays: Scalars["Int"]["output"];
  managedHostname: Scalars["String"]["output"];
  manifestBootstrapError: Scalars["String"]["output"];
  manifestBootstrapStatus: Scalars["String"]["output"];
  manifestHash: Scalars["String"]["output"];
  manifestPath: Scalars["String"]["output"];
  manifestSyncState: Scalars["String"]["output"];
  minimumApprovals: Scalars["Int"]["output"];
  name: Scalars["String"]["output"];
  organizationSlug: Scalars["String"]["output"];
  previewEnabled: Scalars["Boolean"]["output"];
  previewMaxActive: Scalars["Int"]["output"];
  previewScreenshotUrl: Scalars["String"]["output"];
  projectId?: Maybe<Scalars["GUID"]["output"]>;
  projectName: Scalars["String"]["output"];
  projectSlug: Scalars["String"]["output"];
  providerPluginSlug: Scalars["String"]["output"];
  provisioningError: Scalars["String"]["output"];
  provisioningProgress?: Maybe<AstroliftProvisioningProgress>;
  provisioningStatus: Scalars["String"]["output"];
  rawManifest: Scalars["String"]["output"];
  rawManifestStaged: Scalars["String"]["output"];
  rawManifestStagedHash: Scalars["String"]["output"];
  registryRepoUri: Scalars["String"]["output"];
  reprovision: AstroliftAppReprovisionState;
  requiresApproval: Scalars["Boolean"]["output"];
  retentionPolicies: Array<AstroliftRetentionPolicy>;
  securityPolicy: AstroliftSecurityPolicy;
  settingsLastModified?: Maybe<AstroliftAppSettingsLastModified>;
  slug: Scalars["String"]["output"];
  sourceKind: Scalars["String"]["output"];
  sourceRepo: Scalars["String"]["output"];
  sourceUrl: Scalars["String"]["output"];
  sourceWebhookInstalledAt?: Maybe<Scalars["DateTime"]["output"]>;
  stagedEnvChanges: Array<Scalars["String"]["output"]>;
  subdomain: Scalars["String"]["output"];
  teamId?: Maybe<Scalars["GUID"]["output"]>;
  teamName: Scalars["String"]["output"];
  teamSlug: Scalars["String"]["output"];
  /** The app's shape (service, service-data, service-worker, microservices, service-agent, agent, functions, scheduled, task, workflow, mixed), classified from its workloads; null when it has none. Filled on astroliftAppsPage and astroliftMyAppsPage (#2149). */
  topologyKind?: Maybe<Scalars["String"]["output"]>;
  triggerMode: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  version: Scalars["Int"]["output"];
  viewerPermissions: Array<Scalars["String"]["output"]>;
  webhookDeploysPauseReason: Scalars["String"]["output"];
  webhookDeploysPaused: Scalars["Boolean"]["output"];
  webhookDeploysPausedAt?: Maybe<Scalars["DateTime"]["output"]>;
  webhookDeploysPausedByEmail?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftRegisteredAppEntry = {
  appId: Scalars["GUID"]["output"];
  buildContext: Scalars["String"]["output"];
  created: Scalars["Boolean"]["output"];
  manifestPath: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftRegisteredAppMutationResult = {
  data?: Maybe<AstroliftRegisteredApp>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRegisteredAppPage = {
  items: Array<AstroliftRegisteredApp>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page (#2149); null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page (#2149); null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftRegisteredAppStub = {
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftReleaseNotes = {
  baseSha: Scalars["String"]["output"];
  commits: Array<AstroliftReleaseNotesCommit>;
  compareUrl: Scalars["String"]["output"];
  headSha: Scalars["String"]["output"];
  pullRequests: Array<AstroliftReleaseNotesPr>;
};

export type AstroliftReleaseNotesCommit = {
  author: Scalars["String"]["output"];
  isMerge: Scalars["Boolean"]["output"];
  sha: Scalars["String"]["output"];
  subject: Scalars["String"]["output"];
};

export type AstroliftReleaseNotesPr = {
  author: Scalars["String"]["output"];
  body: Scalars["String"]["output"];
  mergedAt?: Maybe<Scalars["String"]["output"]>;
  number: Scalars["Int"]["output"];
  prUrl: Scalars["String"]["output"];
  title: Scalars["String"]["output"];
};

export type AstroliftRemoteRepo = {
  cloneUrlHttps: Scalars["String"]["output"];
  cloneUrlSsh: Scalars["String"]["output"];
  defaultBranch: Scalars["String"]["output"];
  description: Scalars["String"]["output"];
  fullName: Scalars["String"]["output"];
  isArchived: Scalars["Boolean"]["output"];
  isFork: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  pushedAt?: Maybe<Scalars["String"]["output"]>;
  visibility: Scalars["String"]["output"];
  webUrl: Scalars["String"]["output"];
};

export type AstroliftRemoteRepoList = {
  errorCode?: Maybe<Scalars["String"]["output"]>;
  errorMessage?: Maybe<Scalars["String"]["output"]>;
  recoverable: Scalars["Boolean"]["output"];
  repos: Array<AstroliftRemoteRepo>;
};

export type AstroliftRenderedManifest = {
  appSlug: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  error?: Maybe<Scalars["String"]["output"]>;
  errorColumn?: Maybe<Scalars["Int"]["output"]>;
  errorLine?: Maybe<Scalars["Int"]["output"]>;
  errorPath?: Maybe<Scalars["String"]["output"]>;
  imageTag: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
  resources: Scalars["JSON"]["output"];
};

export type AstroliftRerunOnboardingPayload = {
  alreadyRunning: Scalars["Boolean"]["output"];
  detail: Scalars["String"]["output"];
  started: Scalars["Boolean"]["output"];
  workflowId: Scalars["String"]["output"];
};

export type AstroliftRerunOnboardingPayloadMutationResult = {
  data?: Maybe<AstroliftRerunOnboardingPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRetentionPolicy = {
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  retentionDays: Scalars["Int"]["output"];
  signal: Scalars["String"]["output"];
};

export type AstroliftRetentionPolicyMutationResult = {
  data?: Maybe<AstroliftRetentionPolicy>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRetryAutowirePayload = {
  allOk: Scalars["Boolean"]["output"];
  ciWorkflow: Scalars["String"]["output"];
  connected: Scalars["Boolean"]["output"];
  detail: Scalars["String"]["output"];
  secrets: Scalars["String"]["output"];
  webhook: Scalars["String"]["output"];
};

export type AstroliftRetryAutowirePayloadMutationResult = {
  data?: Maybe<AstroliftRetryAutowirePayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRevealedSecret = {
  environmentName: Scalars["String"]["output"];
  key: Scalars["String"]["output"];
  revealedAt: Scalars["DateTime"]["output"];
  secretId: Scalars["String"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftRevealedSecretMutationResult = {
  data?: Maybe<AstroliftRevealedSecret>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRevokeAstroliftSessionPayload = {
  id: Scalars["GUID"]["output"];
  revoked: Scalars["Boolean"]["output"];
};

export type AstroliftRevokeAstroliftSessionPayloadMutationResult = {
  data?: Maybe<AstroliftRevokeAstroliftSessionPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftRole = {
  /** Role bindings on this role inside the viewer's organization. Set by astroliftRolesPage; null where the role is read elsewhere. */
  bindingsCount?: Maybe<Scalars["Int"]["output"]>;
  description: Scalars["String"]["output"];
  /** The role this one was duplicated from. Set by astroliftRole and astroliftRolesPage; null where the role is read elsewhere, and for a role not made by duplicating. */
  duplicatedFrom?: Maybe<AstroliftRoleLineage>;
  id: Scalars["GUID"]["output"];
  isSystem: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  permissions: Array<Scalars["String"]["output"]>;
  scopeLevel: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftRoleBinding = {
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  grantedAt: Scalars["DateTime"]["output"];
  groupExternalId: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  inherits: Scalars["Boolean"]["output"];
  role: AstroliftRole;
  scopeId: Scalars["String"]["output"];
  scopeKind: Scalars["String"]["output"];
  sourceScopeLabel: Scalars["String"]["output"];
  user?: Maybe<AstroliftUser>;
};

export type AstroliftRoleBindingMutationResult = {
  data?: Maybe<AstroliftRoleBinding>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftRoleBindingPage = {
  items: Array<AstroliftRoleBinding>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftRoleBindingsListFilter = {
  /** Who holds the binding: a username, "group:<external id>", or "me": the viewer's own bindings and those on the IdP groups the viewer is in. */
  holder: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** user or group. */
  kind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Role slugs. */
  role: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** ORG, TEAM, PROJECT, APP. */
  scopeKind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Alias of holder, including "me" and "group:<external id>". */
  subject: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftRoleLineage = {
  /** The source role has since been deleted. */
  deleted: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
  isSystem: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  permissions: Array<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
};

export type AstroliftRoleMutationResult = {
  data?: Maybe<AstroliftRole>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftRolePage = {
  items: Array<AstroliftRole>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftRolesListFilter = {
  /** Usernames of who created the role; "me" is the viewer. */
  createdBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** true: the platform's system roles. false: this organization's custom roles. */
  isSystem: InputMaybe<Scalars["Boolean"]["input"]>;
  /** ORG, TEAM, PROJECT, APP. */
  scopeLevel: InputMaybe<Array<Scalars["String"]["input"]>>;
};

/** The run audit's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftRunAuditFilter = {
  /** Agent workload slugs. Only agent runs carry one; other kinds drop out. */
  agent: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** App slugs. */
  app: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** agent, workflow, deployment, job, task. */
  kind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** running, waiting, succeeded, failed, cancelled, unknown (each kind's status, normalised). */
  outcome: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Project slugs: the run's own project, else its app's. */
  project: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** at >= since. */
  since: InputMaybe<Scalars["DateTime"]["input"]>;
  /** The initiator: user pks as strings, or "me". */
  startedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The source's own status word. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** manual, api, schedule, webhook, parent, unknown. */
  trigger: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** at <= until. */
  until: InputMaybe<Scalars["DateTime"]["input"]>;
  /** Workflow definition slugs. Only workflow runs carry one; other kinds drop out. */
  workflow: InputMaybe<Array<Scalars["String"]["input"]>>;
};

/** One run of any kind, as the run audit lists it. */
export type AstroliftRunAuditItem = {
  agentSlug: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  /** When it started, or was created if it has not yet. */
  at: Scalars["DateTime"]["output"];
  durationSeconds?: Maybe<Scalars["Int"]["output"]>;
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentName: Scalars["String"]["output"];
  /** The run's guid, as its own detail query takes it. */
  id: Scalars["String"]["output"];
  /** agent, workflow, deployment, job, task. */
  kind: Scalars["String"]["output"];
  /** running, waiting, succeeded, failed, cancelled, unknown. */
  outcome: Scalars["String"]["output"];
  projectSlug: Scalars["String"]["output"];
  /** The project or app it belongs to. */
  scope: Scalars["String"]["output"];
  /** The source's own trigger word (a deployment's push, ci, rollback, ...). */
  sourceTrigger: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  /** The initiator's name, else a deployment's commit author, else empty. */
  startedByDisplay: Scalars["String"]["output"];
  /** The initiator's user pk, when a person started it. */
  startedById?: Maybe<Scalars["String"]["output"]>;
  /** user, token, schedule, ci, webhook, parent_run or system. */
  startedByKind: Scalars["String"]["output"];
  startedByMe: Scalars["Boolean"]["output"];
  /** The source's own status word. */
  status: Scalars["String"]["output"];
  /** What ran: the agent, workflow, app and environment, job, task. */
  subject: Scalars["String"]["output"];
  /** manual, api, schedule, webhook, parent, unknown. */
  trigger: Scalars["String"]["output"];
  /** The definition slug; a workflow run's page is keyed on it. */
  workflowSlug: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftRunAuditItemPage = {
  items: Array<AstroliftRunAuditItem>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftRunJobOncePayload = {
  logsUrl?: Maybe<Scalars["String"]["output"]>;
  namespace: Scalars["String"]["output"];
  runName: Scalars["String"]["output"];
};

export type AstroliftRunJobOncePayloadMutationResult = {
  data?: Maybe<AstroliftRunJobOncePayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftScanAgentManifestsResult = {
  agents: Array<AstroliftDiscoveredAgentManifest>;
  error?: Maybe<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftScanAppManifestsResult = {
  apps: Array<AstroliftDiscoveredAppManifest>;
  error?: Maybe<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftScheduledJobRun = {
  createdAt: Scalars["DateTime"]["output"];
  durationSeconds?: Maybe<Scalars["Int"]["output"]>;
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentName: Scalars["String"]["output"];
  exitCode?: Maybe<Scalars["Int"]["output"]>;
  id: Scalars["GUID"]["output"];
  k8sJobName: Scalars["String"]["output"];
  logExcerpt: Scalars["String"]["output"];
  output: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggeredByMe: Scalars["Boolean"]["output"];
  triggeredByUserId?: Maybe<Scalars["String"]["output"]>;
  workloadSlug: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftScheduledJobRunPage = {
  items: Array<AstroliftScheduledJobRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

/** The job runs list's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftScheduledJobRunsFilter = {
  /** running, succeeded, failed or superseded. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** scheduled (the cron fired it) or manual (someone ran it now). */
  trigger: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Who ran it now, as triggeredByUserId; "me" is the viewer. */
  triggeredBy: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftScmPushCiWorkflowResult = {
  commitSha: Scalars["String"]["output"];
  filePath: Scalars["String"]["output"];
  repoUrl: Scalars["String"]["output"];
};

export type AstroliftScmPushCiWorkflowResultMutationResult = {
  data?: Maybe<AstroliftScmPushCiWorkflowResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftScmWebhookInstallation = {
  createdAt: Scalars["DateTime"]["output"];
  hookId: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  providerShortCircuited: Scalars["Boolean"]["output"];
  repoFullName: Scalars["String"]["output"];
  sourceConnectionId: Scalars["GUID"]["output"];
  webhookUrl: Scalars["String"]["output"];
};

export type AstroliftScmWebhookInstallationMutationResult = {
  data?: Maybe<AstroliftScmWebhookInstallation>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftScmWebhookSecretReveal = {
  connectionId: Scalars["GUID"]["output"];
  plaintextSecret: Scalars["String"]["output"];
  webhookUrlPath: Scalars["String"]["output"];
};

export type AstroliftScmWebhookSecretRevealMutationResult = {
  data?: Maybe<AstroliftScmWebhookSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftSearchableUser = {
  avatarUrl: Scalars["String"]["output"];
  displayLabel: Scalars["String"]["output"];
  email: Scalars["String"]["output"];
  expiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  invitationId?: Maybe<Scalars["GUID"]["output"]>;
  invitationStatus?: Maybe<Scalars["String"]["output"]>;
  matchKind: Scalars["String"]["output"];
  userId?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftSecretBundle = {
  backendRef: Scalars["String"]["output"];
  clusterSlug?: Maybe<Scalars["String"]["output"]>;
  consumers: Array<AstroliftSecretBundleConsumer>;
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  keyCount: Scalars["Int"]["output"];
  keyNames: Array<Scalars["String"]["output"]>;
  lastKnownKeysAt?: Maybe<Scalars["DateTime"]["output"]>;
  name: Scalars["String"]["output"];
  organizationSlug: Scalars["String"]["output"];
  projectSlug?: Maybe<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
  teamSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftSecretBundleConsumer = {
  consumerKind: Scalars["String"]["output"];
  consumerSlug: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
};

export type AstroliftSecretBundleMutationResult = {
  data?: Maybe<AstroliftSecretBundle>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftSecretBundleReveal = {
  key: Scalars["String"]["output"];
  provider: Scalars["String"]["output"];
  revealedAt: Scalars["DateTime"]["output"];
  value: Scalars["String"]["output"];
};

export type AstroliftSecretBundleRevealMutationResult = {
  data?: Maybe<AstroliftSecretBundleReveal>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftSecretChangeApproval = {
  approverDisplayName: Scalars["String"]["output"];
  approverUserId: Scalars["String"]["output"];
  decidedAt: Scalars["DateTime"]["output"];
  decision: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  reason: Scalars["String"]["output"];
};

export type AstroliftSecretChangeProposal = {
  appliedAt?: Maybe<Scalars["DateTime"]["output"]>;
  applyError: Scalars["String"]["output"];
  approvals: Array<AstroliftSecretChangeApproval>;
  approvalsCount: Scalars["Int"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  decidedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentName: Scalars["String"]["output"];
  expiresAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  op: Scalars["String"]["output"];
  payload: Scalars["JSON"]["output"];
  payloadDiff: Scalars["JSON"]["output"];
  proposerDisplayName: Scalars["String"]["output"];
  proposerUserId: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  requiredApproverCount: Scalars["Int"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftSecretChangeProposalMetadata = {
  appliedAt?: Maybe<Scalars["DateTime"]["output"]>;
  approvalsCount: Scalars["Int"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  decidedAt?: Maybe<Scalars["DateTime"]["output"]>;
  environmentName: Scalars["String"]["output"];
  expiresAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  op: Scalars["String"]["output"];
  proposerDisplayName: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  requiredApproverCount: Scalars["Int"]["output"];
  status: Scalars["String"]["output"];
};

export type AstroliftSecretChangeProposalMutationResult = {
  data?: Maybe<AstroliftSecretChangeProposal>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftSecretChangeProposalPage = {
  complete: Scalars["Boolean"]["output"];
  items: Array<AstroliftSecretChangeProposalMetadata>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftSecretEditor = {
  displayName: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  username: Scalars["String"]["output"];
};

export type AstroliftSecretHistoryActor = {
  id: Scalars["String"]["output"];
  username: Scalars["String"]["output"];
};

export type AstroliftSecretHistoryEntry = {
  action: Scalars["String"]["output"];
  actor: AstroliftSecretHistoryActor;
  errorCode: Scalars["String"]["output"];
  sourceIp: Scalars["String"]["output"];
  success: Scalars["Boolean"]["output"];
  timestamp: Scalars["DateTime"]["output"];
};

export type AstroliftSecurityPolicy = {
  blockOnCriticalCves: Scalars["Boolean"]["output"];
  blockOnHighCveThreshold?: Maybe<Scalars["Int"]["output"]>;
  blockOnMissingSignature: Scalars["Boolean"]["output"];
};

/** Install identity + capabilities handshake (#479). Returned by ``astroliftServerInfo``. Callable by unauthenticated clients so multi-install mobile / CLI / SDK callers can pick the right UI and gate commands before login. */
export type AstroliftServerInfo = {
  apiVersion: Scalars["String"]["output"];
  authMethods: Array<Scalars["String"]["output"]>;
  /** Install-time feature inventory (read-only). Requires a redeploy to change; surfaced for operator awareness. */
  buildTimeFeatures: Array<BuildTimeFeatureInfo>;
  capabilities: Array<Scalars["String"]["output"]>;
  featureFlags: Array<FeatureFlagInfo>;
  installId: Scalars["String"]["output"];
  installLabel?: Maybe<Scalars["String"]["output"]>;
  installSlug: Scalars["String"]["output"];
  region?: Maybe<Scalars["String"]["output"]>;
  /** Current server-side wall clock (UTC). Used by clients to detect clock drift. */
  serverTime: Scalars["DateTime"]["output"];
  version: Scalars["String"]["output"];
};

export type AstroliftSkill = {
  content: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  createdByEmail: Scalars["String"]["output"];
  createdByMe: Scalars["Boolean"]["output"];
  description: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  isGlobal: Scalars["Boolean"]["output"];
  isImported: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  skillVersion: Scalars["Int"]["output"];
  slug: Scalars["String"]["output"];
  sourceKind: Scalars["String"]["output"];
  sourceRef: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftSkillMutationResult = {
  data?: Maybe<AstroliftSkill>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftSkillPage = {
  items: Array<AstroliftSkill>;
  page: Scalars["Int"]["output"];
  pageSize: Scalars["Int"]["output"];
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftSkillsFilter = {
  active: InputMaybe<Scalars["Boolean"]["input"]>;
  /** claude, codex or any. */
  agentType: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** User ids, or "me". */
  createdBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** true: has an import source. */
  imported: InputMaybe<Scalars["Boolean"]["input"]>;
  /** org (the org's own) or global (the platform catalog). */
  scope: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** repo_import, agent_repo, org_repo or catalogue. */
  sourceKind: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftSourceConnection = {
  accountLogin: Scalars["String"]["output"];
  apiBaseUrl: Scalars["String"]["output"];
  appClientId: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  displayName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  installationId: Scalars["String"]["output"];
  isActive: Scalars["Boolean"]["output"];
  isOauthAppConfig: Scalars["Boolean"]["output"];
  isPersonal: Scalars["Boolean"]["output"];
  kind: Scalars["String"]["output"];
  lastUsedAt?: Maybe<Scalars["DateTime"]["output"]>;
  name: Scalars["String"]["output"];
  needsClientId: Scalars["Boolean"]["output"];
  oauthClientId: Scalars["String"]["output"];
  oauthRedirectUri: Scalars["String"]["output"];
  parentOauthAppId?: Maybe<Scalars["GUID"]["output"]>;
  repoVisibilityScopes: Array<Scalars["String"]["output"]>;
  tokenExpiresAt?: Maybe<Scalars["DateTime"]["output"]>;
  updatedAt: Scalars["DateTime"]["output"];
  userUsername?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftSourceConnectionMutationResult = {
  data?: Maybe<AstroliftSourceConnection>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftSourceConnectionPage = {
  items: Array<AstroliftSourceConnection>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftSourceFile = {
  content?: Maybe<Scalars["String"]["output"]>;
  errorCode?: Maybe<Scalars["String"]["output"]>;
  errorMessage?: Maybe<Scalars["String"]["output"]>;
  path: Scalars["String"]["output"];
  recoverable: Scalars["Boolean"]["output"];
  ref: Scalars["String"]["output"];
  repoFullName: Scalars["String"]["output"];
};

export type AstroliftSshDeployKey = {
  createdAt: Scalars["DateTime"]["output"];
  fingerprintSha256: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  lastUsedAt?: Maybe<Scalars["DateTime"]["output"]>;
  name: Scalars["String"]["output"];
  publicKey: Scalars["String"]["output"];
  registeredAppSlug?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftSshDeployKeyCreated = {
  key: AstroliftSshDeployKey;
};

export type AstroliftSshDeployKeyCreatedMutationResult = {
  data?: Maybe<AstroliftSshDeployKeyCreated>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftSshDeployKeyMutationResult = {
  data?: Maybe<AstroliftSshDeployKey>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftSshDeployKeyPage = {
  items: Array<AstroliftSshDeployKey>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftStatusCodeBreakdown = {
  promql: Scalars["String"]["output"];
  rangeSeconds: Scalars["Int"]["output"];
  reason: AstroliftObservabilityPanelReason;
  series: Array<AstroliftStatusCodeSeries>;
};

export type AstroliftStatusCodeSeries = {
  codeClass: Scalars["String"]["output"];
  samples: Array<AstroliftTimeSeriesPoint>;
  topCodes: Array<Scalars["String"]["output"]>;
};

export type AstroliftStep = {
  createdAt: Scalars["DateTime"]["output"];
  env: Scalars["JSON"]["output"];
  id: Scalars["GUID"]["output"];
  jobId: Scalars["GUID"]["output"];
  position: Scalars["Int"]["output"];
  run?: Maybe<Scalars["String"]["output"]>;
  stepId: Scalars["String"]["output"];
  uses?: Maybe<Scalars["String"]["output"]>;
  withParams: Scalars["JSON"]["output"];
};

export type AstroliftStepRun = {
  createdAt: Scalars["DateTime"]["output"];
  exitCode?: Maybe<Scalars["Int"]["output"]>;
  finishedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  step: AstroliftStep;
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftStepRunPage = {
  items: Array<AstroliftStepRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftTaskRun = {
  command: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  durationSeconds?: Maybe<Scalars["Int"]["output"]>;
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  exitCode?: Maybe<Scalars["Int"]["output"]>;
  id: Scalars["GUID"]["output"];
  k8sJobName: Scalars["String"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggeredByUsername?: Maybe<Scalars["String"]["output"]>;
  workloadSlug: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftTaskRunPage = {
  items: Array<AstroliftTaskRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftTaskRunPayload = {
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  registeredAppSlug: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  workloadSlug: Scalars["String"]["output"];
};

export type AstroliftTaskRunPayloadMutationResult = {
  data?: Maybe<AstroliftTaskRunPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftTeam = {
  createdAt: Scalars["DateTime"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  organization: AstroliftOrganization;
  slug: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
};

export type AstroliftTeamMutationResult = {
  data?: Maybe<AstroliftTeam>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftTeamPage = {
  items: Array<AstroliftTeam>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftTeamsListFilter = {
  /** true: teams the viewer is on. false: teams the viewer is not on. */
  mine: InputMaybe<Scalars["Boolean"]["input"]>;
};

export type AstroliftTemplateSendStatPoint = {
  bounces: Scalars["Int"]["output"];
  complaints: Scalars["Int"]["output"];
  deliveries: Scalars["Int"]["output"];
  sends: Scalars["Int"]["output"];
  timestamp: Scalars["DateTime"]["output"];
};

export type AstroliftTenantCluster = {
  agentProvisioned: Scalars["Boolean"]["output"];
  albAuthConfig?: Maybe<Scalars["JSON"]["output"]>;
  authMethod: Scalars["String"]["output"];
  bootstrapRuns: Array<AstroliftClusterBootstrapRun>;
  capabilities: Scalars["JSON"]["output"];
  capabilitiesProbedAt?: Maybe<Scalars["DateTime"]["output"]>;
  createdAt: Scalars["DateTime"]["output"];
  createdByUsername?: Maybe<Scalars["String"]["output"]>;
  endpoint: Scalars["String"]["output"];
  heartbeatAgeSeconds?: Maybe<Scalars["Float"]["output"]>;
  heartbeatIntervalSeconds: Scalars["Int"]["output"];
  heartbeatStatus: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  ingressClass: Scalars["String"]["output"];
  ingressMode: Scalars["String"]["output"];
  isActive: Scalars["Boolean"]["output"];
  lastBootstrapRun?: Maybe<AstroliftClusterBootstrapRun>;
  lastHeartbeatAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastManagementError: Scalars["String"]["output"];
  lifecycle: Scalars["String"]["output"];
  managedAt?: Maybe<Scalars["DateTime"]["output"]>;
  name: Scalars["String"]["output"];
  oidcAuthConfig?: Maybe<Scalars["JSON"]["output"]>;
  organizationSlug?: Maybe<Scalars["String"]["output"]>;
  providerPluginSlug: Scalars["String"]["output"];
  region: Scalars["String"]["output"];
  secretsBackendProvisionedAt?: Maybe<Scalars["DateTime"]["output"]>;
  slug: Scalars["String"]["output"];
};

export type AstroliftTenantClusterBootstrapRunsArgs = {
  limit?: Scalars["Int"]["input"];
};

export type AstroliftTenantClusterMutationResult = {
  data?: Maybe<AstroliftTenantCluster>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftTenantClusterPage = {
  items: Array<AstroliftTenantCluster>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftTimeSeriesPoint = {
  ts: Scalars["DateTime"]["output"];
  value: Scalars["Float"]["output"];
};

export type AstroliftToolDef = {
  adapter: Scalars["String"]["output"];
  capabilityGroup: Scalars["String"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  createdByEmail: Scalars["String"]["output"];
  createdByMe: Scalars["Boolean"]["output"];
  description: Scalars["String"]["output"];
  handlerRef: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  inputSchema: Scalars["JSON"]["output"];
  isBuiltin: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  outputSchema: Scalars["JSON"]["output"];
  skillId?: Maybe<Scalars["GUID"]["output"]>;
  skillIsGlobal: Scalars["Boolean"]["output"];
  skillName: Scalars["String"]["output"];
  skillSlug: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type AstroliftToolDefMutationResult = {
  data?: Maybe<AstroliftToolDef>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftToolDefPage = {
  items: Array<AstroliftToolDef>;
  page: Scalars["Int"]["output"];
  pageSize: Scalars["Int"]["output"];
  totalCount: Scalars["Int"]["output"];
};

export type AstroliftToolDefsFilter = {
  /** python_fn, http_endpoint or mcp_server. */
  adapter: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Pre-installed in the image. */
  builtin: InputMaybe<Scalars["Boolean"]["input"]>;
  capabilityGroup: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** User ids, or "me". */
  createdBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** org or global (the parent skill's). */
  scope: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Parent skill slugs. */
  skill: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftTraceSpan = {
  attributes: Scalars["JSON"]["output"];
  durationMs: Scalars["Float"]["output"];
  operation: Scalars["String"]["output"];
  parentSpanId?: Maybe<Scalars["String"]["output"]>;
  service: Scalars["String"]["output"];
  spanId: Scalars["String"]["output"];
  startTime: Scalars["String"]["output"];
  statusCode: Scalars["String"]["output"];
  traceId: Scalars["String"]["output"];
};

export type AstroliftTrigger = {
  config: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  kind: Scalars["String"]["output"];
  pipelineId: Scalars["GUID"]["output"];
};

export type AstroliftTriggerDeployWorkflowPayload = {
  dispatchedBranch: Scalars["String"]["output"];
  runUrl: Scalars["String"]["output"];
};

export type AstroliftTriggerDeployWorkflowPayloadMutationResult = {
  data?: Maybe<AstroliftTriggerDeployWorkflowPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftTriggerMutationResult = {
  data?: Maybe<AstroliftTrigger>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftUiPreferences = {
  appView: Scalars["String"]["output"];
  /** The person's own partial appearance (ground, accent, density, corners). */
  appearance: Scalars["JSON"]["output"];
  fleetView: Scalars["String"]["output"];
  flowParticles: Scalars["Boolean"]["output"];
  /** The chosen Home layout key; null means the default from access. */
  homeLayout?: Maybe<Scalars["String"]["output"]>;
  /** The first-sign-in layout question was answered, so it is not asked again. */
  homeLayoutAsked: Scalars["Boolean"]["output"];
  /** system, full or reduced. */
  motion: Scalars["String"]["output"];
  /** show or hide: the person's choice, else the organization's default. */
  restrictedSettings: Scalars["String"]["output"];
  /** The person's own show or hide; null follows the organization. */
  restrictedSettingsChoice?: Maybe<Scalars["String"]["output"]>;
  restrictedSettingsOrgDefault: Scalars["String"]["output"];
  workflowView: Scalars["String"]["output"];
};

export type AstroliftUiPreferencesMutationResult = {
  data?: Maybe<AstroliftUiPreferences>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftUser = {
  email: Scalars["String"]["output"];
  id: Scalars["String"]["output"];
  isActive: Scalars["Boolean"]["output"];
  /** Stored anonymous-account state; null when the constructor does not know it. */
  isAnonymized?: Maybe<Scalars["Boolean"]["output"]>;
  username: Scalars["String"]["output"];
};

export type AstroliftUserAlertSubscription = {
  alertKind: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  channel: Scalars["String"]["output"];
  enabled: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
};

export type AstroliftUserAlertSubscriptionMutationResult = {
  data?: Maybe<AstroliftUserAlertSubscription>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftUserProfile = {
  id: Scalars["String"]["output"];
  username?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftValidateCiSecretsPayload = {
  repo: Scalars["String"]["output"];
  results: Array<AstroliftCiSecretValidation>;
};

export type AstroliftValidateCiSecretsPayloadMutationResult = {
  data?: Maybe<AstroliftValidateCiSecretsPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftWebhookDelivery = {
  deliveredAt: Scalars["DateTime"]["output"];
  deliveryId: Scalars["String"]["output"];
  error: Scalars["String"]["output"];
  eventType: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isTest: Scalars["Boolean"]["output"];
  latencyMs: Scalars["Int"]["output"];
  requestPayloadExcerpt: Scalars["String"]["output"];
  responseBodyExcerpt: Scalars["String"]["output"];
  retryAttempt: Scalars["Int"]["output"];
  statusCode?: Maybe<Scalars["Int"]["output"]>;
  subscriptionId: Scalars["GUID"]["output"];
  success: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftWebhookDeliveryPage = {
  items: Array<AstroliftWebhookDelivery>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftWebhookSubscription = {
  createdAt: Scalars["DateTime"]["output"];
  events: Array<Scalars["String"]["output"]>;
  failureCount: Scalars["Int"]["output"];
  format: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  isActive: Scalars["Boolean"]["output"];
  lastDeliveryAt?: Maybe<Scalars["DateTime"]["output"]>;
  lastResponseStatus?: Maybe<Scalars["Int"]["output"]>;
  secretRotatedAt?: Maybe<Scalars["DateTime"]["output"]>;
  url: Scalars["String"]["output"];
  version: Scalars["Int"]["output"];
};

export type AstroliftWebhookSubscriptionMutationResult = {
  data?: Maybe<AstroliftWebhookSubscription>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftWebhookSubscriptionPage = {
  items: Array<AstroliftWebhookSubscription>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftWebhookTestResult = {
  delivered: Scalars["Boolean"]["output"];
  deliveryId: Scalars["String"]["output"];
  durationMs: Scalars["Int"]["output"];
  error: Scalars["String"]["output"];
  responseBodyExcerpt: Scalars["String"]["output"];
  statusCode?: Maybe<Scalars["Int"]["output"]>;
  subscriptionId: Scalars["GUID"]["output"];
  timestamp: Scalars["DateTime"]["output"];
  url: Scalars["String"]["output"];
};

export type AstroliftWebhookTestResultMutationResult = {
  data?: Maybe<AstroliftWebhookTestResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftWorkflowHistoryEvent = {
  decision: Scalars["String"]["output"];
  eventType: Scalars["String"]["output"];
  payload: Scalars["JSON"]["output"];
  retryCount: Scalars["Int"]["output"];
  timestamp: Scalars["String"]["output"];
};

export type AstroliftWorkflowInstance = {
  closedAt: Scalars["String"]["output"];
  durationSeconds?: Maybe<Scalars["Float"]["output"]>;
  runId: Scalars["String"]["output"];
  startedAt: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  taskQueue: Scalars["String"]["output"];
  triggeredBy: Scalars["String"]["output"];
  workflowId: Scalars["String"]["output"];
  workflowType: Scalars["String"]["output"];
};

export type AstroliftWorkflowInstanceDetail = {
  history: Array<AstroliftWorkflowHistoryEvent>;
  instance: AstroliftWorkflowInstance;
};

export type AstroliftWorkflowInstancePage = {
  items: Array<AstroliftWorkflowInstance>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
};

export type AstroliftWorkflowRun = {
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  failure: Scalars["JSON"]["output"];
  id: Scalars["GUID"]["output"];
  organizationId?: Maybe<Scalars["String"]["output"]>;
  registeredAppId?: Maybe<Scalars["String"]["output"]>;
  runId: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  workflowId: Scalars["String"]["output"];
  workflowKind: Scalars["String"]["output"];
};

export type AstroliftWorkload = {
  concurrencyPolicy: Scalars["String"]["output"];
  cpuLimit: Scalars["String"]["output"];
  cpuRequest: Scalars["String"]["output"];
  hpaMaxReplicas?: Maybe<Scalars["Int"]["output"]>;
  hpaMinReplicas?: Maybe<Scalars["Int"]["output"]>;
  hpaTargetCpuPct: Scalars["Int"]["output"];
  id: Scalars["GUID"]["output"];
  inClusterServiceFqdn: Scalars["String"]["output"];
  isPublic: Scalars["Boolean"]["output"];
  kind: Scalars["String"]["output"];
  /** A cron job's most recent run. Null for other kinds, for a job that never ran, and on reads that do not load it (astroliftWorkloads). */
  lastRun?: Maybe<AstroliftCronJobLastRun>;
  memoryLimit: Scalars["String"]["output"];
  memoryRequest: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  ownedByMe: Scalars["Boolean"]["output"];
  ownerUserId?: Maybe<Scalars["String"]["output"]>;
  /** Immutable primary target review facts. Explicit targets require a fresh action-target read. */
  primaryActionTarget?: Maybe<AstroliftWorkloadActionTarget>;
  registeredAppSlug: Scalars["String"]["output"];
  replicas: Scalars["Int"]["output"];
  schedule: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  storageClass: Scalars["String"]["output"];
  storageSize: Scalars["String"]["output"];
  /** Version of this workload for restart and scale preconditions. */
  version: Scalars["Int"]["output"];
  /** Advisory permissions at read time for the current primary environment. Mutations recheck; input, version and driver preconditions still apply. */
  viewerCan: AstroliftWorkloadViewerCan;
  volumes: Scalars["JSON"]["output"];
};

export type AstroliftWorkloadActionTarget = {
  appId: Scalars["GUID"]["output"];
  appVersion: Scalars["Int"]["output"];
  clusterId: Scalars["GUID"]["output"];
  clusterVersion: Scalars["Int"]["output"];
  environmentId: Scalars["GUID"]["output"];
  environmentName: Scalars["String"]["output"];
  environmentVersion: Scalars["Int"]["output"];
  namespace: Scalars["String"]["output"];
  viewerCan: AstroliftWorkloadViewerCan;
  workloadId: Scalars["GUID"]["output"];
  workloadVersion: Scalars["Int"]["output"];
};

export type AstroliftWorkloadIdentityGrant = {
  appliedAt?: Maybe<Scalars["DateTime"]["output"]>;
  assignmentName: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  identityRoleName: Scalars["String"]["output"];
  lastAttemptedAt?: Maybe<Scalars["DateTime"]["output"]>;
  managedServiceId: Scalars["GUID"]["output"];
  providerPluginSlug: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
  roleDefinitionId: Scalars["String"]["output"];
  roleName: Scalars["String"]["output"];
  scope: Scalars["String"]["output"];
  state: Scalars["String"]["output"];
};

export type AstroliftWorkloadManifest = {
  appSlug: Scalars["String"]["output"];
  environmentName: Scalars["String"]["output"];
  error?: Maybe<Scalars["String"]["output"]>;
  errorColumn?: Maybe<Scalars["Int"]["output"]>;
  errorLine?: Maybe<Scalars["Int"]["output"]>;
  errorPath?: Maybe<Scalars["String"]["output"]>;
  imageTag: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
  previousDeploymentId: Scalars["String"]["output"];
  previousImageTag: Scalars["String"]["output"];
  resources: Scalars["JSON"]["output"];
  resourcesPrevious: Scalars["JSON"]["output"];
  workloadSlug: Scalars["String"]["output"];
};

export type AstroliftWorkloadOpPayload = {
  accepted: Scalars["Boolean"]["output"];
  /** Null: the cluster accepted a patch; rollout completion is not established. */
  completed?: Maybe<Scalars["Boolean"]["output"]>;
  desiredReplicas?: Maybe<Scalars["Int"]["output"]>;
  newRevision?: Maybe<Scalars["Int"]["output"]>;
  operationId?: Maybe<Scalars["GUID"]["output"]>;
  readyReplicas?: Maybe<Scalars["Int"]["output"]>;
  target?: Maybe<AstroliftWorkloadActionTarget>;
  workloadId: Scalars["GUID"]["output"];
  workloadVersion?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftWorkloadOpPayloadMutationResult = {
  data?: Maybe<AstroliftWorkloadOpPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AstroliftWorkloadPage = {
  items: Array<AstroliftWorkload>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AstroliftWorkloadPodStatusBucket = {
  count: Scalars["Int"]["output"];
  percent: Scalars["Float"]["output"];
  pods: Array<AstroliftWorkloadPodSummary>;
  status: Scalars["String"]["output"];
};

export type AstroliftWorkloadPodSummary = {
  age?: Maybe<Scalars["DateTime"]["output"]>;
  name: Scalars["String"]["output"];
  ready: Scalars["Boolean"]["output"];
};

export type AstroliftWorkloadResourceGauge = {
  current: Scalars["Float"]["output"];
  limit: Scalars["Float"]["output"];
  percentOfLimit: Scalars["Float"]["output"];
  percentOfRequest: Scalars["Float"]["output"];
  request: Scalars["Float"]["output"];
  unit: Scalars["String"]["output"];
};

export type AstroliftWorkloadResourceUsage = {
  cpu: AstroliftWorkloadResourceGauge;
  memory: AstroliftWorkloadResourceGauge;
  sourcedAt: Scalars["DateTime"]["output"];
};

export type AstroliftWorkloadScalingStatus = {
  currentReplicas: Scalars["Int"]["output"];
  desiredReplicas: Scalars["Int"]["output"];
  hpaEnabled: Scalars["Boolean"]["output"];
  hpaMaxReplicas?: Maybe<Scalars["Int"]["output"]>;
  hpaMinReplicas?: Maybe<Scalars["Int"]["output"]>;
  hpaTargetCpuPct: Scalars["Int"]["output"];
  isScaling: Scalars["Boolean"]["output"];
  replicaLowerBound: Scalars["Int"]["output"];
  replicaUpperBound: Scalars["Int"]["output"];
  sourcedAt: Scalars["DateTime"]["output"];
};

export type AstroliftWorkloadViewerCan = {
  restart: AstroliftActionPermission;
  scale: AstroliftActionPermission;
};

/** The workloads list's declared filters. Unset fields do not filter; list values match any. */
export type AstroliftWorkloadsFilter = {
  /** App slugs. */
  app: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** true: workloads with an ingress. false: internal only. */
  isPublic: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Workload kinds: deployment, cronjob, task, agent, workflow, function, ... */
  kind: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** The owner, as ownerUserId; "me" is the viewer. */
  owner: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type AstroliftZentinelleClusterGateway = {
  clusterId: Scalars["GUID"]["output"];
  clusterSlug: Scalars["String"]["output"];
  credentialRotatedAt?: Maybe<Scalars["DateTime"]["output"]>;
  gatewayDeployed: Scalars["Boolean"]["output"];
  gatewayEnabled: Scalars["Boolean"]["output"];
  gatewayName: Scalars["String"]["output"];
  lastError: Scalars["String"]["output"];
  registeredAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  unregistered: Scalars["Boolean"]["output"];
  zentinelleClusterId: Scalars["String"]["output"];
};

export type AstroliftZentinelleClusterGatewayMutationResult = {
  data?: Maybe<AstroliftZentinelleClusterGateway>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftZentinelleConnection = {
  baseUrl: Scalars["String"]["output"];
  clusters: Array<AstroliftZentinelleClusterGateway>;
  connectedAt?: Maybe<Scalars["DateTime"]["output"]>;
  disconnectedAt?: Maybe<Scalars["DateTime"]["output"]>;
  gatewayFeatureEnabled: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
  lastError: Scalars["String"]["output"];
  status: Scalars["String"]["output"];
  tenantIds: Array<Scalars["String"]["output"]>;
  zentinelleInstallId: Scalars["String"]["output"];
};

export type AstroliftZentinelleConnectionMutationResult = {
  data?: Maybe<AstroliftZentinelleConnection>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AstroliftZentinelleDisconnect = {
  connection: AstroliftZentinelleConnection;
  warnings: Array<Scalars["String"]["output"]>;
};

export type AstroliftZentinelleDisconnectMutationResult = {
  data?: Maybe<AstroliftZentinelleDisconnect>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AttachProjectManagedServiceInput = {
  agentEnvironmentSpecSlug: InputMaybe<Scalars["String"]["input"]>;
  appEnvironmentId: InputMaybe<Scalars["GUID"]["input"]>;
  managedServiceId: Scalars["GUID"]["input"];
};

export type AttachSecretBundleInput = {
  appSlug: Scalars["String"]["input"];
  bundleSlug: Scalars["String"]["input"];
  environmentName: Scalars["String"]["input"];
  prefix: InputMaybe<Scalars["String"]["input"]>;
};

export type Attachmentremovedpayload = {
  attachmentId: Scalars["GUID"]["output"];
  deleted: Scalars["Boolean"]["output"];
  pendingProposalId?: Maybe<Scalars["GUID"]["output"]>;
};

export type AttachmentremovedpayloadMutationResult = {
  data?: Maybe<Attachmentremovedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type AttestSessionInput = {
  attestationObject: InputMaybe<Scalars["String"]["input"]>;
  challenge: Scalars["String"]["input"];
  integrityToken: InputMaybe<Scalars["String"]["input"]>;
  keyId: InputMaybe<Scalars["String"]["input"]>;
  kind: Scalars["String"]["input"];
};

export type AuditLogEntry = {
  errors: Array<Scalars["String"]["output"]>;
  ipAddress?: Maybe<Scalars["String"]["output"]>;
  operation: Scalars["String"]["output"];
  success: Scalars["Boolean"]["output"];
  timestamp: Scalars["DateTime"]["output"];
  username?: Maybe<Scalars["String"]["output"]>;
  variables: Scalars["JSON"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type AuditLogEntryPage = {
  items: Array<AuditLogEntry>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type AvailableTransition = {
  conditionsMet: Scalars["Boolean"]["output"];
  fromState: Scalars["String"]["output"];
  label: Scalars["String"]["output"];
  toState: Scalars["String"]["output"];
};

export type BootstrapOptionOverride = {
  componentKey: Scalars["String"]["input"];
  optionKey: Scalars["String"]["input"];
  value: Scalars["String"]["input"];
};

export type Bootstraprunrecordedpayload = {
  id: Scalars["GUID"]["output"];
};

export type BootstraprunrecordedpayloadMutationResult = {
  data?: Maybe<Bootstraprunrecordedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type BringClusterIntoManagementInputType = {
  clusterId: Scalars["GUID"]["input"];
};

/** Install-time (build-time) platform feature reported by ``astroliftServerInfo``. These gate app / schema loading at boot and are NOT runtime-toggleable — changing one requires a redeploy. ``envVar`` is the environment variable that controls it. */
export type BuildTimeFeatureInfo = {
  description?: Maybe<Scalars["String"]["output"]>;
  enabled: Scalars["Boolean"]["output"];
  envVar: Scalars["String"]["output"];
  key: Scalars["String"]["output"];
};

export type BulkAppResultItem = {
  appSlug: Scalars["String"]["output"];
  errors: Array<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
};

export type BulkApproveDeploymentsInput = {
  deploymentIds: Array<Scalars["GUID"]["input"]>;
  reason: InputMaybe<Scalars["String"]["input"]>;
};

export type BulkAssignTeamMemberRolesInput = {
  memberIds: Array<Scalars["GUID"]["input"]>;
  roleId: Scalars["GUID"]["input"];
  teamId: Scalars["GUID"]["input"];
};

export type BulkImportAppSecretsInput = {
  appSlug: Scalars["String"]["input"];
  dotenvText: Scalars["String"]["input"];
};

export type BulkOperationResult = {
  failedCount: Scalars["Int"]["output"];
  okCount: Scalars["Int"]["output"];
  perApp: Array<BulkAppResultItem>;
};

export type BulkPushSecretsInput = {
  appSlugs: Array<Scalars["String"]["input"]>;
  bundleSlug: Scalars["String"]["input"];
  environmentName: InputMaybe<Scalars["String"]["input"]>;
};

export type BulkRejectDeploymentsInput = {
  deploymentIds: Array<Scalars["GUID"]["input"]>;
  reason: Scalars["String"]["input"];
};

export type BulkResyncManifestInput = {
  appSlugs: Array<Scalars["String"]["input"]>;
};

export type BulkRevokeRoleBindingsInput = {
  bindingIds: Array<Scalars["GUID"]["input"]>;
};

export type BulkRollingRestartInput = {
  appSlugs: Array<Scalars["String"]["input"]>;
  environmentName: InputMaybe<Scalars["String"]["input"]>;
};

export type Bulkimportpayload = {
  appSlug: Scalars["String"]["output"];
  keysSet: Array<Scalars["String"]["output"]>;
  rawManifestStaged: Scalars["String"]["output"];
};

export type BulkimportpayloadMutationResult = {
  data?: Maybe<Bulkimportpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type CancelDeregisterInput = {
  reason: InputMaybe<Scalars["String"]["input"]>;
  workflowId: Scalars["String"]["input"];
};

export type CatalogueState = "AVAILABLE" | "NO_DATA" | "RATE_LIMITED" | "UNAVAILABLE";

export type CiWorkflowSyncActionInput = {
  appId: Scalars["GUID"]["input"];
};

export type ClearAlertSubscriptionInput = {
  id: Scalars["GUID"]["input"];
};

export type ClearEnvironmentSettingInput = {
  environmentId: Scalars["GUID"]["input"];
  key: Scalars["String"]["input"];
};

export type CloneWorkflowDefinitionResult = {
  definition?: Maybe<WorkflowDefinitionType>;
  errors: Array<ValidationError>;
  ok: Scalars["Boolean"]["output"];
  slug?: Maybe<Scalars["String"]["output"]>;
};

export type CloudOrphanReportType = {
  complete: Scalars["Boolean"]["output"];
  incompleteKinds: Array<Scalars["String"]["output"]>;
  orphans: Array<CloudOrphanType>;
  scannedKinds: Array<Scalars["String"]["output"]>;
};

export type CloudOrphanType = {
  classification: Scalars["String"]["output"];
  clusterSlug: Scalars["String"]["output"];
  identifier: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  reapKey: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
};

export type ClusterAuthUserRefInput = {
  clusterId: Scalars["GUID"]["input"];
  expectedSource: InputMaybe<ExpectedClusterAuthSourceInput>;
  expectedUserId: InputMaybe<Scalars["String"]["input"]>;
  username: Scalars["String"]["input"];
};

export type ClusterModelDensity = {
  capacity: ModelClusterCapacity;
  clusterId: Scalars["GUID"]["output"];
  end: Scalars["DateTime"]["output"];
  inventoryLimit: Scalars["Int"]["output"];
  items: Array<SharedModelDensityRow>;
  modelCount: Scalars["Int"]["output"];
  retrievedAt: Scalars["DateTime"]["output"];
  returnedCount: Scalars["Int"]["output"];
  scope: Scalars["String"]["output"];
  source: Scalars["String"]["output"];
  start: Scalars["DateTime"]["output"];
  truncated: Scalars["Boolean"]["output"];
};

export type ClusterModelDeployment = {
  appliedResources?: Maybe<ModelResources>;
  appliedSubscriptionRevision: Scalars["Int"]["output"];
  clusterId: Scalars["GUID"]["output"];
  clusterName: Scalars["String"]["output"];
  clusterSlug: Scalars["String"]["output"];
  computeMode?: Maybe<Scalars["String"]["output"]>;
  desiredResources: ModelResources;
  desiredSubscriptionRevision: Scalars["Int"]["output"];
  id: Scalars["GUID"]["output"];
  modelRepo: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  operationCompletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  operationId?: Maybe<Scalars["String"]["output"]>;
  operationStartedAt?: Maybe<Scalars["DateTime"]["output"]>;
  organizationId: Scalars["GUID"]["output"];
  providerId: Scalars["GUID"]["output"];
  readinessGeneration?: Maybe<Scalars["Int"]["output"]>;
  readinessObservedAt?: Maybe<Scalars["DateTime"]["output"]>;
  ready?: Maybe<Scalars["Boolean"]["output"]>;
  reason?: Maybe<Scalars["String"]["output"]>;
  revisionSha?: Maybe<Scalars["String"]["output"]>;
  runtimeReason?: Maybe<Scalars["String"]["output"]>;
  runtimeSupported?: Maybe<Scalars["Boolean"]["output"]>;
  status: Scalars["String"]["output"];
  subscriptionsEnabled: Scalars["Boolean"]["output"];
  version: Scalars["Int"]["output"];
};

export type ClusterModelDeploymentMutationResult = {
  data?: Maybe<ClusterModelDeployment>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type ClusterModelDeploymentPage = {
  items: Array<ClusterModelDeployment>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type ClusterModelsFilterInput = {
  clusterId: InputMaybe<Scalars["GUID"]["input"]>;
  computeMode: InputMaybe<Scalars["String"]["input"]>;
  deployedByMe: InputMaybe<Scalars["Boolean"]["input"]>;
  ready: InputMaybe<Scalars["Boolean"]["input"]>;
  status: InputMaybe<Scalars["String"]["input"]>;
  subscriptionsEnabled: InputMaybe<Scalars["Boolean"]["input"]>;
};

export type Clusteragentkeyissuedpayload = {
  agentKey: Scalars["String"]["output"];
  clusterId: Scalars["GUID"]["output"];
  heartbeatUrl: Scalars["String"]["output"];
  intervalSeconds: Scalars["Int"]["output"];
  rotated: Scalars["Boolean"]["output"];
};

export type ClusteragentkeyissuedpayloadMutationResult = {
  data?: Maybe<Clusteragentkeyissuedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ConfigureProviderPluginInput = {
  config: Scalars["JSON"]["input"];
  organizationScoped: Scalars["Boolean"]["input"];
  pluginSlug: Scalars["String"]["input"];
};

export type ConfiguredWorkflow = {
  createdAt: Scalars["DateTime"]["output"];
  definitionName: Scalars["String"]["output"];
  definitionSlug: Scalars["String"]["output"];
  description: Scalars["String"]["output"];
  guid: Scalars["String"]["output"];
  inputs: Scalars["JSON"]["output"];
  isEnabled: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  organizationGuid?: Maybe<Scalars["String"]["output"]>;
  patternKind: Scalars["String"]["output"];
  runCount: Scalars["Int"]["output"];
  runs: Array<WorkflowRun>;
  scheduleCron?: Maybe<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
  stageBindings: Scalars["JSON"]["output"];
  triggerKind: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type ConfiguredWorkflowPage = {
  items: Array<ConfiguredWorkflow>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type ConfirmUploadResult = {
  ack: Scalars["Boolean"]["output"];
};

export type ConnectExistingGithubAppInput = {
  apiBaseUrl: InputMaybe<Scalars["String"]["input"]>;
  appId: Scalars["String"]["input"];
  clientId: InputMaybe<Scalars["String"]["input"]>;
  clientSecret: InputMaybe<Scalars["String"]["input"]>;
  orgLogin: InputMaybe<Scalars["String"]["input"]>;
  privateKeyPem: Scalars["String"]["input"];
  webhookSecret: InputMaybe<Scalars["String"]["input"]>;
};

export type ConnectSourceInput = {
  accountLogin: InputMaybe<Scalars["String"]["input"]>;
  apiBaseUrl: InputMaybe<Scalars["String"]["input"]>;
  appClientId: InputMaybe<Scalars["String"]["input"]>;
  displayName: InputMaybe<Scalars["String"]["input"]>;
  installationId: InputMaybe<Scalars["String"]["input"]>;
  kind: Scalars["String"]["input"];
  oauthClientId: InputMaybe<Scalars["String"]["input"]>;
  oauthRedirectUri: InputMaybe<Scalars["String"]["input"]>;
  repoVisibilityScopes: InputMaybe<Array<Scalars["String"]["input"]>>;
  secretPlaintext: Scalars["String"]["input"];
};

export type ConnectUserSourceProviderInput = {
  providerConfigId: Scalars["GUID"]["input"];
  returnTo: InputMaybe<Scalars["String"]["input"]>;
};

export type ConnectZentinelleInput = {
  enrollmentCode: Scalars["String"]["input"];
  url: Scalars["String"]["input"];
};

export type CostWindow = "D7" | "D30" | "H24" | "MTD";

export type CreateAgentEnvironmentSpecInput = {
  agentType: Scalars["String"]["input"];
  allowInstall: Scalars["Boolean"]["input"];
  boxWorkspace: Scalars["Boolean"]["input"];
  configBranch: Scalars["String"]["input"];
  configManifestPath: Scalars["String"]["input"];
  configRepo: Scalars["String"]["input"];
  envVars: InputMaybe<Scalars["JSON"]["input"]>;
  gpu: Scalars["Int"]["input"];
  gpuType: Scalars["String"]["input"];
  imageTag: Scalars["String"]["input"];
  managedModel: Scalars["Boolean"]["input"];
  migProfile: Scalars["String"]["input"];
  modelGateway: Scalars["Boolean"]["input"];
  name: Scalars["String"]["input"];
  projectId: InputMaybe<Scalars["GUID"]["input"]>;
  runAsNonRoot: Scalars["Boolean"]["input"];
  runtime: Scalars["String"]["input"];
  secretRefs: InputMaybe<Scalars["JSON"]["input"]>;
  slug: Scalars["String"]["input"];
  teamId: InputMaybe<Scalars["GUID"]["input"]>;
  toolPreset: Scalars["String"]["input"];
  vncEnabled: Scalars["Boolean"]["input"];
};

export type CreateAlertRuleInput = {
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  managedServiceId: InputMaybe<Scalars["GUID"]["input"]>;
  name: Scalars["String"]["input"];
  notifyChannels: InputMaybe<Scalars["JSON"]["input"]>;
  predicate: InputMaybe<Scalars["JSON"]["input"]>;
  severity: InputMaybe<Scalars["String"]["input"]>;
  target: Scalars["String"]["input"];
  targetId: InputMaybe<Scalars["String"]["input"]>;
};

export type CreateApiTokenInput = {
  expiresInDays: InputMaybe<Scalars["Int"]["input"]>;
  name: Scalars["String"]["input"];
  scopes: InputMaybe<Array<Scalars["String"]["input"]>>;
  teamSlug: InputMaybe<Scalars["String"]["input"]>;
};

export type CreateClusterAuthGroupInput = {
  clusterId: Scalars["GUID"]["input"];
  description: Scalars["String"]["input"];
  expectedSource: InputMaybe<ExpectedClusterAuthSourceInput>;
  name: Scalars["String"]["input"];
};

export type CreateClusterAuthUserInput = {
  clusterId: Scalars["GUID"]["input"];
  email: Scalars["String"]["input"];
  expectedSource: InputMaybe<ExpectedClusterAuthSourceInput>;
  groups: Array<Scalars["String"]["input"]>;
  password: InputMaybe<Scalars["String"]["input"]>;
  permanent: Scalars["Boolean"]["input"];
};

export type CreateDeployTokenInput = {
  appSlug: Scalars["String"]["input"];
  expiresAtIso: InputMaybe<Scalars["String"]["input"]>;
  name: Scalars["String"]["input"];
  scopes: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type CreateEmailTemplateInput = {
  htmlBody: Scalars["String"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
  subject: Scalars["String"]["input"];
  textBody: Scalars["String"]["input"];
};

export type CreateGroupRoleMappingInput = {
  groupExternalId: Scalars["String"]["input"];
  roleId: Scalars["GUID"]["input"];
  scopeGuid: Scalars["GUID"]["input"];
  scopeKind: Scalars["String"]["input"];
};

export type CreateIdentityProviderInput = {
  clientId: InputMaybe<Scalars["String"]["input"]>;
  clientSecretRef: InputMaybe<Scalars["String"]["input"]>;
  config: InputMaybe<Scalars["JSON"]["input"]>;
  displayName: InputMaybe<Scalars["String"]["input"]>;
  kind: Scalars["String"]["input"];
  metadataUrl: InputMaybe<Scalars["String"]["input"]>;
  oidcDiscoveryUrl: InputMaybe<Scalars["String"]["input"]>;
  setActive: Scalars["Boolean"]["input"];
};

export type CreateInvitationInput = {
  email: Scalars["String"]["input"];
  expiresInDays: InputMaybe<Scalars["Int"]["input"]>;
  roleSlug: InputMaybe<Scalars["String"]["input"]>;
};

export type CreateManagedDomainInput = {
  defaultFor: Scalars["String"]["input"];
  dnsConfig: InputMaybe<Scalars["JSON"]["input"]>;
  dnsDriver: Scalars["String"]["input"];
  isWildcardManaged: Scalars["Boolean"]["input"];
  organizationScoped: Scalars["Boolean"]["input"];
  zone: Scalars["String"]["input"];
};

export type CreateOrganizationInput = {
  name: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
  website: InputMaybe<Scalars["String"]["input"]>;
};

export type CreatePipelineInput = {
  defaultBranch: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
  repoUrl: Scalars["String"]["input"];
  tomlPath: Scalars["String"]["input"];
};

export type CreatePolicyInput = {
  actionPattern: Scalars["String"]["input"];
  actorPattern: InputMaybe<Scalars["JSON"]["input"]>;
  conditions: InputMaybe<Scalars["JSON"]["input"]>;
  description: InputMaybe<Scalars["String"]["input"]>;
  effect: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
  resourcePattern: InputMaybe<Scalars["JSON"]["input"]>;
  scopeId: InputMaybe<Scalars["Int"]["input"]>;
  scopeLevel: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
};

export type CreatePreviewEnvironmentInput = {
  appSlug: Scalars["String"]["input"];
  branch: Scalars["String"]["input"];
  environmentName: Scalars["String"]["input"];
};

export type CreateProjectInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  name: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
  teamId: Scalars["GUID"]["input"];
};

export type CreateProjectSecretBundleInput = {
  backendRef: InputMaybe<Scalars["String"]["input"]>;
  clusterId: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
  projectId: Scalars["GUID"]["input"];
  slug: Scalars["String"]["input"];
};

export type CreateRoleInput = {
  description: Scalars["String"]["input"];
  duplicatedFromId: InputMaybe<Scalars["GUID"]["input"]>;
  name: Scalars["String"]["input"];
  permissions: Array<Scalars["String"]["input"]>;
  scopeLevel: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
};

export type CreateTeamInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  name: Scalars["String"]["input"];
  organizationId: Scalars["GUID"]["input"];
  slug: Scalars["String"]["input"];
};

export type CreateTriggerInput = {
  config: Scalars["String"]["input"];
  kind: Scalars["String"]["input"];
  pipelineId: Scalars["GUID"]["input"];
};

export type CreateWebhookSubscriptionInput = {
  appSlug: InputMaybe<Scalars["String"]["input"]>;
  events: Array<Scalars["String"]["input"]>;
  format: InputMaybe<Scalars["String"]["input"]>;
  teamSlug: InputMaybe<Scalars["String"]["input"]>;
  url: Scalars["String"]["input"];
};

export type CreateWorkflowResult = {
  errors: Array<ValidationError>;
  ok: Scalars["Boolean"]["output"];
  workflow?: Maybe<ConfiguredWorkflow>;
};

export type CreateWorkflowStageResult = {
  errors: Array<ValidationError>;
  ok: Scalars["Boolean"]["output"];
  stage?: Maybe<WorkflowStageType>;
};

export type CreateWorkflowTriggerResult = {
  endpoint?: Maybe<Scalars["String"]["output"]>;
  errors: Array<ValidationError>;
  ok: Scalars["Boolean"]["output"];
  signingSecret?: Maybe<Scalars["String"]["output"]>;
  slug?: Maybe<Scalars["String"]["output"]>;
};

export type DataImportUploadResult = {
  id?: Maybe<Scalars["ID"]["output"]>;
  preSignedUrl?: Maybe<Scalars["String"]["output"]>;
  publicUrl?: Maybe<Scalars["String"]["output"]>;
};

export type DecommissionClusterInputType = {
  clusterId: Scalars["GUID"]["input"];
  deleteCloudInfra: Scalars["Boolean"]["input"];
};

export type DeleteAlertRuleInput = {
  id: Scalars["GUID"]["input"];
};

export type DeleteAppDnsRecordInput = {
  appId: Scalars["GUID"]["input"];
  hostname: Scalars["String"]["input"];
  recordType: Scalars["String"]["input"];
};

export type DeleteAppIdentityRoleInput = {
  appId: Scalars["GUID"]["input"];
};

export type DeleteAppIngressInput = {
  appId: Scalars["GUID"]["input"];
  hostname: InputMaybe<Scalars["String"]["input"]>;
};

export type DeleteAppSecretInput = {
  appSlug: Scalars["String"]["input"];
  key: Scalars["String"]["input"];
};

export type DeleteEmailTemplateInput = {
  managedServiceId: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
};

export type DeleteFormDefinitionInput = {
  slug: Scalars["String"]["input"];
};

export type DeleteGroupRoleMappingInput = {
  id: Scalars["GUID"]["input"];
};

export type DeletePipelineSecretInput = {
  name: Scalars["String"]["input"];
  pipelineId: Scalars["GUID"]["input"];
};

export type DeleteRoleInput = {
  id: Scalars["GUID"]["input"];
};

export type DeleteSshDeployKeyInput = {
  id: Scalars["GUID"]["input"];
};

export type DeleteWebhookSubscriptionInput = {
  id: Scalars["GUID"]["input"];
};

export type DependencyObservationState = "AVAILABLE" | "NO_DATA" | "UNAVAILABLE";

export type DeployClusterAgentInput = {
  clusterId: Scalars["GUID"]["input"];
};

export type DeployTokenSecretReveal = {
  plaintextSecret: Scalars["String"]["output"];
  rotationGraceSeconds: Scalars["Int"]["output"];
  token: AstroliftDeployToken;
};

export type DeployTokenSecretRevealMutationResult = {
  data?: Maybe<DeployTokenSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type DeploymentByIdInput = {
  id: Scalars["GUID"]["input"];
};

export type Deploytokenrevokedpayload = {
  id: Scalars["GUID"]["output"];
  revoked: Scalars["Boolean"]["output"];
};

export type DeploytokenrevokedpayloadMutationResult = {
  data?: Maybe<Deploytokenrevokedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type DeprovisionClusterModelInput = {
  deleteData: Scalars["Boolean"]["input"];
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  id: Scalars["GUID"]["input"];
  ifMatchVersion: Scalars["Int"]["input"];
  organizationId: Scalars["GUID"]["input"];
};

export type DeprovisionManagedServiceInput = {
  deleteData: Scalars["Boolean"]["input"];
  forceDestroy: Scalars["Boolean"]["input"];
  id: Scalars["GUID"]["input"];
};

export type DeregisterAppInput = {
  appSlug: Scalars["String"]["input"];
  confirmName: Scalars["String"]["input"];
  deleteData: Scalars["Boolean"]["input"];
  forceDestroy: Scalars["Boolean"]["input"];
};

export type DetachProjectManagedServiceInput = {
  attachmentId: Scalars["GUID"]["input"];
};

export type DetachSecretBundleInput = {
  attachmentId: Scalars["GUID"]["input"];
};

export type DisconnectSourceInput = {
  id: Scalars["GUID"]["input"];
};

export type DisconnectUserSourceProviderInput = {
  confirmAccountLogin: Scalars["String"]["input"];
  providerConfigId: Scalars["GUID"]["input"];
};

export type DisconnectZentinelleInput = {
  force: Scalars["Boolean"]["input"];
};

export type DomainPathRouteInput = {
  pathPrefix: Scalars["String"]["input"];
  priority: Scalars["Int"]["input"];
  stripPrefix: Scalars["Boolean"]["input"];
  targetPort: Scalars["Int"]["input"];
  targetWorkloadSlug: Scalars["String"]["input"];
};

export type DomainRedirectRuleInput = {
  destinationUrl: Scalars["String"]["input"];
  httpStatus: Scalars["Int"]["input"];
  kind: Scalars["String"]["input"];
  preserveQueryString: Scalars["Boolean"]["input"];
  priority: Scalars["Int"]["input"];
  sourcePattern: Scalars["String"]["input"];
};

export type ElevateAdminSessionInput = {
  credential: Scalars["String"]["input"];
  method: Scalars["String"]["input"];
  ttlSeconds: InputMaybe<Scalars["Int"]["input"]>;
};

export type Emailsuppressionaddpayload = {
  address: Scalars["String"]["output"];
  reason: Scalars["String"]["output"];
};

export type EmailsuppressionaddpayloadMutationResult = {
  data?: Maybe<Emailsuppressionaddpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Emailsuppressionremovepayload = {
  address: Scalars["String"]["output"];
  removed: Scalars["Boolean"]["output"];
};

export type EmailsuppressionremovepayloadMutationResult = {
  data?: Maybe<Emailsuppressionremovepayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Emailtemplatedeletedpayload = {
  deleted: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
};

export type EmailtemplatedeletedpayloadMutationResult = {
  data?: Maybe<Emailtemplatedeletedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type EnsureAgentBoxInput = {
  agentSlug: Scalars["String"]["input"];
  environmentSpecSlug: Scalars["String"]["input"];
  idleTimeoutSeconds: InputMaybe<Scalars["Int"]["input"]>;
  image: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
};

export type EntityType = "COMPONENTS" | "EMPLOYEE" | "FIELDFLO" | "PERMISSIONS" | "SITE_LABEL";

export type EnvironmentByIdInput = {
  id: Scalars["GUID"]["input"];
};

export type ExpectedClusterAuthSourceInput = {
  providerPluginId: Scalars["GUID"]["input"];
  providerPoolId: Scalars["String"]["input"];
  sourceVersion: Scalars["String"]["input"];
};

export type ExportAppLogsInput = {
  appSlug: Scalars["String"]["input"];
  container: InputMaybe<Scalars["String"]["input"]>;
  environmentName: InputMaybe<Scalars["String"]["input"]>;
  format: Scalars["String"]["input"];
  level: InputMaybe<Scalars["String"]["input"]>;
  podName: InputMaybe<Scalars["String"]["input"]>;
  regex: InputMaybe<Scalars["String"]["input"]>;
  since: InputMaybe<Scalars["DateTime"]["input"]>;
  until: InputMaybe<Scalars["DateTime"]["input"]>;
  workloadSlug: InputMaybe<Scalars["String"]["input"]>;
};

export type ExportAuditEventsInput = {
  action: InputMaybe<Scalars["String"]["input"]>;
  actorId: InputMaybe<Scalars["String"]["input"]>;
  createdAtGte: InputMaybe<Scalars["DateTime"]["input"]>;
  createdAtLte: InputMaybe<Scalars["DateTime"]["input"]>;
  decision: InputMaybe<Scalars["String"]["input"]>;
  filter: InputMaybe<AstroliftAuditEventsFilter>;
  format: Scalars["String"]["input"];
  search: InputMaybe<Scalars["String"]["input"]>;
  subjectUserId: InputMaybe<Scalars["String"]["input"]>;
  targetId: InputMaybe<Scalars["String"]["input"]>;
  targetKind: InputMaybe<Scalars["String"]["input"]>;
};

export type ExtendPreviewTtlInputGql = {
  days: Scalars["Int"]["input"];
  id: Scalars["GUID"]["input"];
};

/** Runtime feature toggle reported by ``astroliftServerInfo``. */
export type FeatureFlagInfo = {
  description?: Maybe<Scalars["String"]["output"]>;
  enabled: Scalars["Boolean"]["output"];
  key: Scalars["String"]["output"];
};

export type FeatureFlagInfoMutationResult = {
  data?: Maybe<FeatureFlagInfo>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type FileUploadResult = {
  id?: Maybe<Scalars["ID"]["output"]>;
  preSignedUrl?: Maybe<Scalars["String"]["output"]>;
  publicUrl?: Maybe<Scalars["String"]["output"]>;
};

export type ForceRedeployInput = {
  appSlug: Scalars["String"]["input"];
  confirmSlug: Scalars["String"]["input"];
  environmentName: InputMaybe<Scalars["String"]["input"]>;
};

export type ForecastConfidence = "HIGH" | "LOW" | "MEDIUM";

export type FormDefinitionInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  fieldConfig: InputMaybe<Scalars["JSON"]["input"]>;
  formType: InputMaybe<Scalars["String"]["input"]>;
  isPublic: InputMaybe<Scalars["Boolean"]["input"]>;
  logicRules: InputMaybe<Scalars["JSON"]["input"]>;
  name: Scalars["String"]["input"];
  schema: Scalars["JSON"]["input"];
  scoring: InputMaybe<Scalars["JSON"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type FormDefinitionUpdateInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  fieldConfig: InputMaybe<Scalars["JSON"]["input"]>;
  formType: InputMaybe<Scalars["String"]["input"]>;
  isPublic: InputMaybe<Scalars["Boolean"]["input"]>;
  logicRules: InputMaybe<Scalars["JSON"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  schema: InputMaybe<Scalars["JSON"]["input"]>;
  scoring: InputMaybe<Scalars["JSON"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type GenerateInstallEnrollmentQrInput = {
  label: InputMaybe<Scalars["String"]["input"]>;
  ttlSeconds: InputMaybe<Scalars["Int"]["input"]>;
};

export type GenerateSshDeployKeyInput = {
  appSlug: InputMaybe<Scalars["String"]["input"]>;
  name: Scalars["String"]["input"];
};

export type GoldenSignalKind =
  | "ERRORS"
  | "LATENCY_P50"
  | "LATENCY_P90"
  | "LATENCY_P95"
  | "LATENCY_P99"
  | "SATURATION_CPU"
  | "SATURATION_MEMORY"
  | "TRAFFIC";

export type GrantRoleInput = {
  expiresAt: InputMaybe<Scalars["DateTime"]["input"]>;
  groupExternalId: InputMaybe<Scalars["String"]["input"]>;
  roleId: Scalars["GUID"]["input"];
  scopeGuid: Scalars["GUID"]["input"];
  scopeKind: Scalars["String"]["input"];
  userId: InputMaybe<Scalars["String"]["input"]>;
};

export type GrantTeamAccessInput = {
  accessLevel: Scalars["String"]["input"];
  appId: Scalars["GUID"]["input"];
  teamId: Scalars["GUID"]["input"];
};

export type GroupOperationInput = {
  groupId: Scalars["ID"]["input"];
  operation: Scalars["String"]["input"];
  userIds: Array<Scalars["ID"]["input"]>;
};

export type HuggingFaceModel = {
  architectures: Array<Scalars["String"]["output"]>;
  author?: Maybe<Scalars["String"]["output"]>;
  compatibility: ModelCompatibility;
  downloads?: Maybe<Scalars["Float"]["output"]>;
  gated: ModelGating;
  library?: Maybe<Scalars["String"]["output"]>;
  license?: Maybe<Scalars["String"]["output"]>;
  likes?: Maybe<Scalars["Float"]["output"]>;
  pipelineTag?: Maybe<Scalars["String"]["output"]>;
  repoId: Scalars["String"]["output"];
  revisionSha?: Maybe<Scalars["String"]["output"]>;
};

export type HuggingFaceModelResult = {
  model?: Maybe<HuggingFaceModel>;
  observedAt: Scalars["DateTime"]["output"];
  retryAfterSeconds?: Maybe<Scalars["Int"]["output"]>;
  source: Scalars["String"]["output"];
  state: CatalogueState;
};

export type HuggingFaceModelsPage = {
  items: Array<HuggingFaceModel>;
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  observedAt: Scalars["DateTime"]["output"];
  retryAfterSeconds?: Maybe<Scalars["Int"]["output"]>;
  source: Scalars["String"]["output"];
  state: CatalogueState;
};

export type ImportGapType = {
  code: Scalars["String"]["output"];
  message: Scalars["String"]["output"];
  nodeId?: Maybe<Scalars["String"]["output"]>;
  nodeType?: Maybe<Scalars["String"]["output"]>;
  severity: Scalars["String"]["output"];
};

export type ImportWorkflowFlowResult = {
  createdSlug?: Maybe<Scalars["String"]["output"]>;
  definition?: Maybe<WorkflowManifestDefinitionType>;
  errors: Array<ValidationError>;
  gaps: Array<ImportGapType>;
  ok: Scalars["Boolean"]["output"];
  preview: Scalars["Boolean"]["output"];
  stages: Array<WorkflowManifestStageType>;
};

export type ImportWorkflowManifestResult = {
  createdSlug?: Maybe<Scalars["String"]["output"]>;
  errors: Array<ValidationError>;
  manifest?: Maybe<WorkflowManifestPreviewType>;
  mode?: Maybe<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  repointedSlugs: Array<Scalars["String"]["output"]>;
};

export type InstallClusterPrereqsInputType = {
  clusterId: Scalars["GUID"]["input"];
  optionOverrides: Array<BootstrapOptionOverride>;
  selectedComponents: Array<Scalars["String"]["input"]>;
};

export type InstallScmWebhookInput = {
  connectionId: Scalars["GUID"]["input"];
  repoFullName: Scalars["String"]["input"];
  secret: InputMaybe<Scalars["String"]["input"]>;
  targetUrl: InputMaybe<Scalars["String"]["input"]>;
};

export type InstallSourceWebhookInput = {
  appSlug: Scalars["String"]["input"];
};

export type IssueClusterAgentKeyInput = {
  clusterId: Scalars["GUID"]["input"];
  intervalSeconds: InputMaybe<Scalars["Int"]["input"]>;
};

export type LibraryMkdirResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars["Boolean"]["output"];
};

export type LibraryRenameDirectoryResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars["Boolean"]["output"];
};

export type LibraryRenameFileResult = {
  file?: Maybe<UploadType>;
  ok: Scalars["Boolean"]["output"];
};

export type LibraryRmFileResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars["Boolean"]["output"];
};

export type LibrarySetIconResult = {
  directory?: Maybe<SharedDirectoryType>;
  ok: Scalars["Boolean"]["output"];
};

export type LoginResult = {
  user?: Maybe<UserType>;
};

export type LogoutAllSessionsInput = {
  keepCurrent: Scalars["Boolean"]["input"];
};

export type Managedresourceadoptionpayload = {
  acknowledgedPriorOwner: Scalars["String"]["output"];
  actorDisplay: Scalars["String"]["output"];
  adoptedAt: Scalars["DateTime"]["output"];
  classification: Scalars["String"]["output"];
  cloud: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  managedServiceId: Scalars["GUID"]["output"];
  priorBindingId: Scalars["String"]["output"];
  priorManagedBy: Scalars["String"]["output"];
  priorManagedServiceId: Scalars["String"]["output"];
  priorMarkers: Scalars["JSON"]["output"];
  reason: Scalars["String"]["output"];
  resourceId: Scalars["String"]["output"];
  stampedMarkers: Scalars["JSON"]["output"];
  status: Scalars["String"]["output"];
  surface: Scalars["String"]["output"];
};

export type ManagedresourceadoptionpayloadMutationResult = {
  data?: Maybe<Managedresourceadoptionpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Managedservicedeletedpayload = {
  deleted: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
};

export type ManagedservicedeletedpayloadMutationResult = {
  data?: Maybe<Managedservicedeletedpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Manifestpushpayload = {
  branchName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  note: Scalars["String"]["output"];
  prUrl: Scalars["String"]["output"];
};

export type ManifestpushpayloadMutationResult = {
  data?: Maybe<Manifestpushpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type Manifeststagepayload = {
  id: Scalars["GUID"]["output"];
  rawManifest: Scalars["String"]["output"];
  rawManifestStaged: Scalars["String"]["output"];
  syncState: Scalars["String"]["output"];
};

export type ManifeststagepayloadMutationResult = {
  data?: Maybe<Manifeststagepayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type MarkNotificationReadInput = {
  id: Scalars["GUID"]["input"];
};

export type MarkOnboardingCompleteInput = {
  skip: Scalars["Boolean"]["input"];
};

export type Markallreadpayload = {
  marked: Scalars["Int"]["output"];
};

export type MarkallreadpayloadMutationResult = {
  data?: Maybe<Markallreadpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type MigrateAppInputGql = {
  appEnvironmentId: Scalars["GUID"]["input"];
  drainSource: Scalars["Boolean"]["input"];
  targetClusterId: Scalars["GUID"]["input"];
};

export type ModelClusterCapacity = {
  cpuCores?: Maybe<Scalars["Float"]["output"]>;
  freshnessSeconds: Scalars["Int"]["output"];
  gpuDevices: Array<ModelGpuCapacity>;
  memoryBytes?: Maybe<Scalars["Float"]["output"]>;
  observedAt?: Maybe<Scalars["DateTime"]["output"]>;
  source: Scalars["String"]["output"];
  state: ModelObservationState;
  vramBytes?: Maybe<Scalars["Float"]["output"]>;
};

export type ModelCompatibility = "UNKNOWN";

export type ModelDeploymentMetrics = {
  clusterId: Scalars["GUID"]["output"];
  end: Scalars["DateTime"]["output"];
  metrics: Array<ModelMetricObservation>;
  retrievedAt: Scalars["DateTime"]["output"];
  sampleLimit: Scalars["Int"]["output"];
  scope: Scalars["String"]["output"];
  serviceId: Scalars["GUID"]["output"];
  start: Scalars["DateTime"]["output"];
  stepSeconds: Scalars["Int"]["output"];
};

export type ModelGating = "AUTO" | "MANUAL" | "NONE" | "UNKNOWN";

export type ModelGpuCapacity = {
  devices: Scalars["Int"]["output"];
  resource: Scalars["String"]["output"];
};

export type ModelMetricObservation = {
  aggregationWindowSeconds: Scalars["Int"]["output"];
  key: Scalars["String"]["output"];
  observedAt?: Maybe<Scalars["DateTime"]["output"]>;
  samples: Array<ModelObservationSample>;
  source: Scalars["String"]["output"];
  state: ModelObservationState;
  unit: Scalars["String"]["output"];
  value?: Maybe<Scalars["Float"]["output"]>;
};

export type ModelObservationSample = {
  timestamp: Scalars["DateTime"]["output"];
  value: Scalars["Float"]["output"];
};

export type ModelObservationState =
  | "AVAILABLE"
  | "NO_DATA"
  | "STALE"
  | "UNAVAILABLE"
  | "UNCONFIGURED"
  | "UNSUPPORTED";

export type ModelPlacementCluster = {
  id: Scalars["GUID"]["output"];
  name: Scalars["String"]["output"];
  providerId: Scalars["GUID"]["output"];
  region?: Maybe<Scalars["String"]["output"]>;
  slug: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type ModelPlacementClusterPage = {
  items: Array<ModelPlacementCluster>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type ModelResourceRequests = {
  cpuCoresPerReplica?: Maybe<Scalars["Float"]["output"]>;
  gpuDevicesPerReplica?: Maybe<Scalars["Int"]["output"]>;
  gpuResource?: Maybe<Scalars["String"]["output"]>;
  memoryBytesPerReplica?: Maybe<Scalars["Float"]["output"]>;
  observedAt?: Maybe<Scalars["DateTime"]["output"]>;
  replicas?: Maybe<Scalars["Int"]["output"]>;
  source: Scalars["String"]["output"];
  totalCpuCores?: Maybe<Scalars["Float"]["output"]>;
  totalGpuDevices?: Maybe<Scalars["Int"]["output"]>;
  totalMemoryBytes?: Maybe<Scalars["Float"]["output"]>;
};

export type ModelResources = {
  cpuKvCacheGiB?: Maybe<Scalars["Int"]["output"]>;
  cpuRequest?: Maybe<Scalars["String"]["output"]>;
  gpuCount?: Maybe<Scalars["Int"]["output"]>;
  memoryRequest?: Maybe<Scalars["String"]["output"]>;
  replicas?: Maybe<Scalars["Int"]["output"]>;
};

export type ModelRuntimeAdmission = {
  architecture?: Maybe<Scalars["String"]["output"]>;
  eligible: Scalars["Boolean"]["output"];
  hardwareAdmission: Scalars["String"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
  runtimeVersion?: Maybe<Scalars["String"]["output"]>;
};

export type ModelSubscription = {
  alias: Scalars["String"]["output"];
  appId: Scalars["GUID"]["output"];
  appName: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  appliedRevision: Scalars["Int"]["output"];
  bindingPrefix: Scalars["String"]["output"];
  canRevoke: Scalars["Boolean"]["output"];
  desiredEnabled: Scalars["Boolean"]["output"];
  desiredRevision: Scalars["Int"]["output"];
  environmentId: Scalars["GUID"]["output"];
  environmentName: Scalars["String"]["output"];
  id: Scalars["GUID"]["output"];
  modelDeploymentId: Scalars["GUID"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
  reconcileStartedAt?: Maybe<Scalars["DateTime"]["output"]>;
  reconciledAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  version: Scalars["Int"]["output"];
};

export type ModelSubscriptionOperation = {
  deployment: ClusterModelDeployment;
  restartRequired: Scalars["Boolean"]["output"];
  subscription: ModelSubscription;
};

export type ModelSubscriptionOperationMutationResult = {
  data?: Maybe<ModelSubscriptionOperation>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type ModelSubscriptionPage = {
  items: Array<ModelSubscription>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type ModelSubscriptionTarget = {
  appId: Scalars["GUID"]["output"];
  appName: Scalars["String"]["output"];
  appSlug: Scalars["String"]["output"];
  clusterId: Scalars["GUID"]["output"];
  eligible: Scalars["Boolean"]["output"];
  environmentId: Scalars["GUID"]["output"];
  environmentName: Scalars["String"]["output"];
  environmentVersion: Scalars["Int"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
};

/** One page of a cursor-paginated or numbered list. */
export type ModelSubscriptionTargetPage = {
  items: Array<ModelSubscriptionTarget>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type MoveAppToTeamInput = {
  appId: Scalars["GUID"]["input"];
  targetTeamId: Scalars["GUID"]["input"];
};

export type Mutation = {
  abortDeployment: AstroliftDeploymentMutationResult;
  acceptInvitation: AstroliftInvitationMutationResult;
  acknowledgeAlertEvent: AstroliftAlertEventMutationResult;
  /** Activate or deactivate an object by its global ID. */
  activate: Scalars["Boolean"]["output"];
  addAppDomain: AstroliftAppDomainMutationResult;
  addEmailSuppressionEntry: EmailsuppressionaddpayloadMutationResult;
  addOrganizationAllowlistDomain: AstroliftOrganizationAllowlistedDomainMutationResult;
  addWildcardDomain: AstroliftAppDomainMutationResult;
  adoptManagedResource: ManagedresourceadoptionpayloadMutationResult;
  adoptRepoCiWorkflow: AstroliftCiWorkflowSyncStatusMutationResult;
  applyStagedManifest: ManifeststagepayloadMutationResult;
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
  attachAgentSecretBundle: AstroliftAgentSecretBundleAttachmentMutationResult;
  attachProjectManagedService: AstroliftManagedServiceAttachmentMutationResult;
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
  clearAgentQuarantine: NoneTypeMutationResult;
  clearAlertSubscription: AstroliftUserAlertSubscriptionMutationResult;
  clearEnvironmentSetting: AstroliftEnvironmentSettingMutationResult;
  /** Deep-copy a visible workflow definition + its stages into the caller's org as a new editable definition (spec 40 §2.1). New slug on collision; global stages copy with agent_definition cleared. */
  cloneWorkflowDefinition: CloneWorkflowDefinitionResult;
  configureAgentTaskCallbacks: AgentTaskCallbackPolicyMutationResult;
  configureProviderPlugin: ProviderpluginconfigpayloadMutationResult;
  /** Confirm or update a previously uploaded file. Set delete=true to soft-delete the upload. */
  confirmPreSignedUrlImageUpload: ConfirmUploadResult;
  connectExistingGithubApp: AstroliftSourceConnectionMutationResult;
  connectSource: AstroliftSourceConnectionMutationResult;
  connectZentinelle: AstroliftZentinelleConnectionMutationResult;
  /** Cancel, terminate, or retry cleanup for an exact owned execution. */
  controlWorkflowExecution: WorkflowExecutionControlResult;
  createAgentEnvironmentSpec: AstroliftAgentEnvironmentSpecMutationResult;
  createAgentSecretBundle: AstroliftAgentSecretBundleMutationResult;
  createAgentTrigger: AstroliftAgentTriggerResult;
  createAlertRule: AstroliftAlertRuleMutationResult;
  createApiToken: AstroliftApiTokenPlaintextMutationResult;
  createClusterAuthGroup: AstroliftClusterAuthUserChangeMutationResult;
  createClusterAuthUser: AstroliftClusterAuthUserMutationResult;
  createDeployToken: DeployTokenSecretRevealMutationResult;
  createEmailTemplate: AstroliftEmailTemplateMutationResult;
  createFormDefinition: AstroliftFormDefinitionMutationResult;
  createGroupRoleMapping: AstroliftGroupRoleMappingMutationResult;
  createIdentityProvider: AstroliftIdentityProviderMutationResult;
  createInvitation: AstroliftInvitationCreatedMutationResult;
  createManagedDomain: AstroliftManagedDomainMutationResult;
  createOrganization: AstroliftOrganizationMutationResult;
  createPipeline: AstroliftPipelineMutationResult;
  createPolicy: AstroliftPolicyMutationResult;
  createPreviewEnvironment: AstroliftPreviewEnvironmentMutationResult;
  createProject: AstroliftProjectMutationResult;
  createProjectSecretBundle: AstroliftSecretBundleMutationResult;
  createRole: AstroliftRoleMutationResult;
  createSkill: AstroliftSkillMutationResult;
  createTeam: AstroliftTeamMutationResult;
  createToolDef: AstroliftToolDefMutationResult;
  createTrigger: AstroliftTriggerMutationResult;
  createWebhookSubscription: WebhookSecretRevealMutationResult;
  /** Create a configured Workflow from a visible definition (spec 40 §2.2). */
  createWorkflow: CreateWorkflowResult;
  /** Create a new workflow definition (platform operator only). */
  createWorkflowDefinition: MutationResult;
  /** Add a stage to a writable workflow definition. order=null appends after the definition's last stage. */
  createWorkflowStage: CreateWorkflowStageResult;
  /** Create an inbound webhook trigger for a workflow definition (platform operator only). */
  createWorkflowTrigger: CreateWorkflowTriggerResult;
  decommissionCluster: AstroliftTenantClusterMutationResult;
  deelevateAdminSession: AstroliftDeelevatePayloadMutationResult;
  /** Delete an object by its global ID (soft-delete via delete_check). */
  delete: Scalars["Boolean"]["output"];
  deleteAgentBundleSecretValue: AstroliftAgentSecretBundleMutationResult;
  deleteAgentEnvironmentSpec: AstroliftAgentEnvironmentSpecMutationResult;
  deleteAgentSecretBundle: AstroliftAgentSecretBundleMutationResult;
  deleteAgentSecretValue: AstroliftAgentSecretStatusMutationResult;
  deleteAlertRule: AlertruledeletedpayloadMutationResult;
  deleteAppDnsRecord: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppIdentityRole: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppIngress: AstroliftCapabilityDeprovisionPayloadMutationResult;
  deleteAppSecret: AppsecretwritepayloadMutationResult;
  deleteClusterAuthUser: AstroliftClusterAuthUserChangeMutationResult;
  deleteDeployment: AstroliftDeploymentMutationResult;
  deleteEmailTemplate: EmailtemplatedeletedpayloadMutationResult;
  deleteFormDefinition: AstroliftFormDefinitionMutationResult;
  deleteGroupRoleMapping: SoftdeletepayloadMutationResult;
  deleteInvitation: AstroliftInvitationMutationResult;
  deletePipeline: AstroliftPipelineMutationResult;
  deletePipelineSecret: AstroliftPipelineSecretChangeMutationResult;
  deleteProjectBundleSecretValue: AstroliftSecretBundleMutationResult;
  deleteProjectSecretBundle: AstroliftSecretBundleMutationResult;
  deleteSkill: AstroliftSkillMutationResult;
  deleteSshDeployKey: AstroliftSshDeployKeyMutationResult;
  deleteToolDef: AstroliftToolDefMutationResult;
  deleteWebhookSubscription: SoftdeletepayloadMutationResult;
  /** Soft-delete a configured Workflow and tear down its schedule. */
  deleteWorkflow: MutationResult;
  /** Soft-delete an org-owned workflow definition by slug. */
  deleteWorkflowDefinition: MutationResult;
  /** Soft-delete a stage from a writable definition (spec 40 §6). */
  deleteWorkflowStage: MutationResult;
  deployClusterAgent: AstroliftTenantClusterMutationResult;
  deprovisionClusterModel: ClusterModelDeploymentMutationResult;
  deprovisionManagedService: ManagedservicedeletedpayloadMutationResult;
  deprovisionProjectManagedService: ManagedservicedeletedpayloadMutationResult;
  deregisterAstroliftApp: AstroliftDeregisterAppPayloadMutationResult;
  destroyAgentBox: AstroliftAgentBoxMutationResult;
  detachAgentSecretBundle: AstroliftAgentSecretBundleAttachmentMutationResult;
  detachProjectManagedService: AstroliftManagedServiceAttachmentMutationResult;
  detachSecretBundle: AttachmentremovedpayloadMutationResult;
  disconnectSource: AstroliftSourceConnectionMutationResult;
  disconnectZentinelle: AstroliftZentinelleDisconnectMutationResult;
  elevateAdminSession: AstroliftElevatePayloadMutationResult;
  ensureAgentBox: AstroliftAgentBoxMutationResult;
  exportAstroliftAppLogs: AstroliftAppLogExportMutationResult;
  exportAuditEvents: AstroliftAuditExportMutationResult;
  extendPreviewTtl: AstroliftPreviewEnvironmentMutationResult;
  /** Upload a file and get a pre-signed URL. Creates a FileUpload wrapper around the Upload. */
  fileUpload: FileUploadResult;
  forceAstroliftRedeploy: AstroliftForceRedeployPayloadMutationResult;
  generateInstallEnrollmentQr: AstroliftEnrollmentQrPayloadMutationResult;
  /** Generate a temporary authentication token for Rocket.Chat. TTL is configured on the Rocket.Chat server. */
  generateRocketChatToken: Scalars["String"]["output"];
  generateSshDeployKey: AstroliftSshDeployKeyCreatedMutationResult;
  grantRole: AstroliftRoleBindingMutationResult;
  grantTeamAccessToApp: AstroliftAppTeamAccessMutationResult;
  heartbeatSession: AstroliftHeartbeatSessionPayloadMutationResult;
  importSkillsFromRepo: AstroliftImportSkillsResultMutationResult;
  /** Import a popular visual agent/workflow builder export (Langflow, Flowise, …) into an Astrolift WorkflowDefinition. preview=true (default) returns the mapped manifest + gap report without persisting; preview=false creates an org-scoped, disabled definition + stages. */
  importWorkflowFlow: ImportWorkflowFlowResult;
  /** Import a workflow manifest TOML. preview=true (default) returns the parsed shape without persisting; preview=false creates a disabled, org-scoped WorkflowDefinition + stages in the caller's org and returns the (possibly uniquified) slug. replace=true instead upserts the org's own definition sharing the manifest's slug: in place when the stage kinds are unchanged (configured Workflows, bindings and schedules all keep working untouched), otherwise as a new version with every configured Workflow repointed to it, or a clear refusal when a repoint would break one's bindings. */
  importWorkflowManifest: ImportWorkflowManifestResult;
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
  libraryRmdir: Scalars["Boolean"]["output"];
  /** Set the icon for a library directory. */
  librarySetIcon: LibrarySetIconResult;
  /** Authenticate a user with username and password. */
  login: LoginResult;
  /** Logout the current user. */
  logout: Scalars["Boolean"]["output"];
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
  notificationRead: Scalars["Boolean"]["output"];
  openCiWorkflowReconcilePr: AstroliftCiWorkflowSyncStatusMutationResult;
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
  pinTransaction: Scalars["Boolean"]["output"];
  /** Update or set the user's PIN. */
  pinUpdate: Scalars["Boolean"]["output"];
  placeObservabilityRetentionHold: ObservabilityRetentionHoldTypeMutationResult;
  /** Get a pre-signed URL for uploading an image or file. Optionally attach it to an entity via owner_container_property. */
  preSignedUrlImageUpload: PreSignedUrlUploadResult;
  /** Process a previously uploaded data import file. */
  processFile: ProcessFileResult;
  /** Update any user's profile via ProfileSerializer. Platform operator only; self-service profile edits go through updateMyProfile. */
  profile: MutationResult;
  /** Upload an image for a specific profile image field (avatar, signature). */
  profileImageFieldUpload: ProfileImageFieldUploadResult;
  /** Request deletion of a user account. Only the platform operator may delete another user. */
  profileRequestDeleteUser: Scalars["Boolean"]["output"];
  /** Request a password reset email. Only the platform operator may send one to another user. */
  profileRequestPwdChange: Scalars["Boolean"]["output"];
  promoteDeployment: AstroliftDeploymentMutationResult;
  proposeSecretChange: AstroliftSecretChangeProposalMutationResult;
  provisionClusterModel: ClusterModelDeploymentMutationResult;
  provisionManagedDomain: ProvisionManagedDomainPayloadMutationResult;
  provisionManagedService: AstroliftManagedServiceMutationResult;
  provisionProjectManagedService: AstroliftManagedServiceMutationResult;
  publishForm: AstroliftFormDefinitionMutationResult;
  pullCiWorkflowFromRepo: AstroliftCiWorkflowSyncStatusMutationResult;
  pushAstroliftCiSecretsToRepo: AstroliftPushCiSecretsPayloadMutationResult;
  pushAstroliftCiWorkflowToRepo: AstroliftPushCiWorkflowPayloadMutationResult;
  pushCiWorkflow: AstroliftScmPushCiWorkflowResultMutationResult;
  pushManifestToRepo: ManifestpushpayloadMutationResult;
  reapCloudOrphan: ReapCloudOrphanPayloadMutationResult;
  recheckDomainValidation: AstroliftAppDomainMutationResult;
  reconcileClusterIngresses: ReconcileClusterIngressesResultMutationResult;
  recordClusterBootstrapRun: BootstraprunrecordedpayloadMutationResult;
  redeliverAgentTaskCallback: AstroliftAgentTaskMutationResult;
  redeployApp: AstroliftDeploymentMutationResult;
  refreshCiWorkflowSyncStatus: AstroliftCiWorkflowSyncStatusMutationResult;
  refreshClusterManagement: AstroliftTenantClusterMutationResult;
  registerAgentRepo: AstroliftRegisterAgentRepoResultMutationResult;
  registerApp: AstroliftRegisteredAppMutationResult;
  registerAppRepo: AstroliftRegisterAppRepoResultMutationResult;
  registerMobileDevice: AstroliftDeviceRegistrationMutationResult;
  registerOrgSkillRepo: AstroliftOrgSkillRepoMutationResult;
  registerTenantCluster: AstroliftTenantClusterMutationResult;
  registerZentinelleCluster: AstroliftZentinelleClusterGatewayMutationResult;
  reissueManagedDomainCert: ReissueManagedDomainCertPayloadMutationResult;
  rejectDeployment: AstroliftDeploymentMutationResult;
  rejectDeploymentByToken: AstroliftDeploymentMutationResult;
  rejectSecretChange: AstroliftSecretChangeProposalMutationResult;
  releaseObservabilityRetentionHold: ObservabilityRetentionHoldTypeMutationResult;
  removeAgentSecretRef: AstroliftAgentSecretStatusMutationResult;
  removeAppDomain: AppdomainremovedpayloadMutationResult;
  removeEmailSuppressionEntry: EmailsuppressionremovepayloadMutationResult;
  removeOrgSkillRepo: AstroliftOrgSkillRepoMutationResult;
  removeOrganizationAllowlistDomain: SoftdeletepayloadMutationResult;
  /** Reorder a definition's stages (spec 40 §6). Pass stage guids in the new order. */
  reorderWorkflowStages: MutationResult;
  replyAgentTaskInput: AstroliftAgentTaskInputReplyMutationResult;
  reprovisionManagedService: AstroliftManagedServiceMutationResult;
  reprovisionProjectManagedService: AstroliftManagedServiceMutationResult;
  requestAttestationChallenge: AstroliftAttestationChallengePayloadMutationResult;
  requestQuotaIncrease: AstroliftQuotaIncreaseRequestMutationResult;
  rerunAstroliftOnboarding: AstroliftRerunOnboardingPayloadMutationResult;
  resendInvitation: AstroliftInvitationCreatedMutationResult;
  resetClusterAuthUserPassword: AstroliftClusterAuthUserChangeMutationResult;
  restartAstroliftWorkload: AstroliftWorkloadOpPayloadMutationResult;
  restoreApp: AstroliftRegisteredAppMutationResult;
  resumeAppIngress: AstroliftAppEnvironmentMutationResult;
  resumeAstroliftAppWebhookDeploys: AstroliftRegisteredAppMutationResult;
  resumeEnvironment: AstroliftAppEnvironmentMutationResult;
  resyncAllAstroliftCiWorkflows: AstroliftCiWorkflowResyncAllResultMutationResult;
  resyncAstroliftCiWorkflow: AstroliftCiWorkflowSyncStatusMutationResult;
  resyncAstroliftManifestFromRepo: ResyncManifestPayloadMutationResult;
  retryAgentTask: AstroliftAgentTaskMutationResult;
  retryAstroliftAutowire: AstroliftRetryAutowirePayloadMutationResult;
  revalidateManagedDomain: RevalidateManagedDomainPayloadMutationResult;
  revealAgentBundleSecretValue: AstroliftAgentSecretRevealMutationResult;
  revealAgentSecretValue: AstroliftAgentSecretRevealMutationResult;
  revealAppSecret: AstroliftRevealedSecretMutationResult;
  revealManagedServiceConnection: AstroliftManagedServiceConnectionMutationResult;
  revealProjectBundleSecretValue: AstroliftSecretBundleRevealMutationResult;
  revokeApiToken: SoftdeletepayloadMutationResult;
  revokeAppCertificate: AstroliftCapabilityDeprovisionPayloadMutationResult;
  revokeAstroliftSession: AstroliftRevokeAstroliftSessionPayloadMutationResult;
  revokeDeployToken: DeploytokenrevokedpayloadMutationResult;
  revokeInvitation: AstroliftInvitationMutationResult;
  revokeMobileDevice: RevokemobiledevicepayloadMutationResult;
  revokeModelSubscription: ModelSubscriptionOperationMutationResult;
  revokeRoleBinding: SoftdeletepayloadMutationResult;
  revokeTeamAccessFromApp: SoftdeletepayloadMutationResult;
  rollbackDeployment: AstroliftDeploymentMutationResult;
  rotateAppSecret: AppsecretwritepayloadMutationResult;
  rotateDeployToken: DeployTokenSecretRevealMutationResult;
  rotateOutboundWebhookSecret: WebhookSecretRevealMutationResult;
  rotateSecretBundle: AstroliftSecretBundleMutationResult;
  rotateWebhookSecret: AstroliftScmWebhookSecretRevealMutationResult;
  rotateZentinelleGatewayCredential: AstroliftZentinelleClusterGatewayMutationResult;
  runAstroliftAgent: AstroliftAgentTaskMutationResult;
  runAstroliftJobOnce: AstroliftRunJobOncePayloadMutationResult;
  runTask: AstroliftTaskRunPayloadMutationResult;
  /** Run a configured Workflow now via Temporal (spec 40 §3). */
  runWorkflow: RunWorkflowResult;
  /** Compatibility entry for reviewed Definition starts. Requires exact definitionId, revision, input schema digest, caller requestId and confirmation; prefer startWorkflowDefinition. */
  runWorkflowDefinition: RunWorkflowDefinitionResult;
  scaleAstroliftWorkload: AstroliftWorkloadOpPayloadMutationResult;
  scaleServiceAgent: AstroliftAgentScaleResult;
  sendAgentTaskInput: AstroliftAgentTaskInputMessageMutationResult;
  sendManagedServiceTestEmail: AstroliftManagedServiceTestEmailResultMutationResult;
  setActiveIdentityProvider: AstroliftIdentityProviderMutationResult;
  setAgentBundleSecretValue: AstroliftAgentSecretBundleMutationResult;
  setAgentSecretValue: AstroliftAgentSecretStatusMutationResult;
  setAgentTaskCallbackSecret: AgentTaskCallbackSecretMutationResult;
  setAlertSubscription: AstroliftUserAlertSubscriptionMutationResult;
  setAppAccess: AstroliftAppAccessMutationResult;
  setAppSecret: AppsecretwritepayloadMutationResult;
  setAppSecretMetadata: AppsecretmetadatapayloadMutationResult;
  setAppSubdomain: AstroliftRegisteredAppMutationResult;
  setClusterAuthUserEnabled: AstroliftClusterAuthUserChangeMutationResult;
  setClusterAuthUserGroups: AstroliftClusterAuthUserChangeMutationResult;
  setClusterAuthUserPassword: AstroliftClusterAuthUserChangeMutationResult;
  setDomainPathRoutes: AstroliftAppDomainMutationResult;
  setDomainRedirects: AstroliftAppDomainMutationResult;
  setEnvironmentSetting: AstroliftEnvironmentSettingMutationResult;
  /** Toggle a public runtime feature flag (the admin 'feature flipper'). Platform-admin only. ``key`` is the public dotted key from ``astroliftServerInfo.featureFlags`` (e.g. ``zentinelle.enabled``); the backing Constance value is set and the updated flag is returned. Unknown / non-public keys are rejected with a VALIDATION error. Install-time features (``astroliftServerInfo.buildTimeFeatures``) are NOT settable here — they require a redeploy. */
  setFeatureFlag: FeatureFlagInfoMutationResult;
  setNotificationPreference: AstroliftNotificationPreferenceMutationResult;
  setNotificationProfile: AstroliftNotificationProfileMutationResult;
  /** Turn a per-organization module on or off for the active organization (org admins: ``org.update``). ``key`` is ``chat_studio_integration``, ``agent_live_attach`` or ``chat_studio_agent_runs``. Turning on a module the install admin has forced off (``astroliftServerInfo.featureFlags``, ``modules.*_allowed``) is refused with PRECONDITION; turning one off always succeeds. */
  setOrganizationModule: AstroliftOrganizationModuleMutationResult;
  setPipelineSecret: AstroliftPipelineSecretChangeMutationResult;
  setPreviewPinned: AstroliftPreviewEnvironmentMutationResult;
  setProjectBundleSecretValue: AstroliftSecretBundleMutationResult;
  setRetentionPolicy: AstroliftRetentionPolicyMutationResult;
  setZentinelleGatewayEnabled: AstroliftZentinelleClusterGatewayMutationResult;
  /** Cancel a sign request. Sign requests are not available: this always refuses. */
  signRequestCancel: Scalars["Boolean"]["output"];
  /** Sign a sign request. Sign requests are not available: this always refuses. */
  signRequestSign: Scalars["Boolean"]["output"];
  /** Request a sign from a user. Sign requests are not available: this always refuses. */
  signRequestUser: Scalars["Boolean"]["output"];
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
  startPipelineRun: AstroliftPipelineRunMutationResult;
  /** Start a workflow for an object. */
  startWorkflow: StartWorkflowResult;
  /** Start the exact reviewed definition with declared inputs and an actor-scoped requestId; retry the same request after an uncertain response. */
  startWorkflowDefinition: WorkflowDefinitionStartMutationResult;
  submitForm: AstroliftFormSubmissionMutationResult;
  subscribeClusterModel: ModelSubscriptionOperationMutationResult;
  /** Switch the active user (impersonation). */
  switchUser: SwitchUserResult;
  syncManifestFromRepo: ManifeststagepayloadMutationResult;
  tearDownApp: SoftdeletepayloadMutationResult;
  tearDownPreview: AstroliftDeploymentMutationResult;
  terminateWorkflowInstance: MutationResult;
  testModelEndpoint: AstroliftModelEndpointTestMutationResult;
  testNotificationChannel: AstroliftNotificationMutationResult;
  testSharedModelEndpoint: AstroliftModelEndpointTestMutationResult;
  testWebhookSubscription: AstroliftWebhookTestResultMutationResult;
  transferApp: AstroliftRegisteredAppMutationResult;
  /** Transition a workflow instance to a new state. */
  transitionWorkflow: MutationResult;
  triggerAstroliftDeployWorkflow: AstroliftTriggerDeployWorkflowPayloadMutationResult;
  triggerPipelineRun: AstroliftPipelineRunMutationResult;
  unbindAgentTrigger: AstroliftAgentTriggerResult;
  unmuteAlertRule: AstroliftAlertRuleMutationResult;
  unregisterTenantCluster: SoftdeletepayloadMutationResult;
  unregisterZentinelleCluster: AstroliftZentinelleClusterGatewayMutationResult;
  updateAgentEnvironmentSpec: AstroliftAgentEnvironmentSpecMutationResult;
  updateAgentRunSpec: AstroliftAgentRunSpecMutationResult;
  updateAgentSecretBundle: AstroliftAgentSecretBundleMutationResult;
  updateAlertRule: AstroliftAlertRuleMutationResult;
  updateApp: AstroliftRegisteredAppMutationResult;
  updateAstroliftSecurityPolicy: AstroliftRegisteredAppMutationResult;
  updateClusterModel: ClusterModelDeploymentMutationResult;
  updateEmailTemplate: AstroliftEmailTemplateMutationResult;
  updateFormDefinition: AstroliftFormDefinitionMutationResult;
  updateIdentityProvider: AstroliftIdentityProviderMutationResult;
  updateManagedDomain: AstroliftManagedDomainMutationResult;
  updateManagedService: AstroliftManagedServiceMutationResult;
  updateManifest: ManifeststagepayloadMutationResult;
  updateMyProfile: AstroliftMyProfileMutationResult;
  updateMyUiPreferences: AstroliftUiPreferencesMutationResult;
  updateOrgSkillRepo: AstroliftOrgSkillRepoMutationResult;
  updateOrganization: AstroliftOrganizationMutationResult;
  updatePipeline: AstroliftPipelineMutationResult;
  updatePolicy: AstroliftPolicyMutationResult;
  updateProject: AstroliftProjectMutationResult;
  updateProjectManagedService: AstroliftManagedServiceMutationResult;
  updateProjectSecretBundle: AstroliftSecretBundleMutationResult;
  updateRole: AstroliftRoleMutationResult;
  updateRoleBinding: AstroliftRoleBindingMutationResult;
  updateSkill: AstroliftSkillMutationResult;
  updateSourceConnection: AstroliftSourceConnectionMutationResult;
  updateSubmissionStatus: AstroliftFormSubmissionMutationResult;
  updateTeam: AstroliftTeamMutationResult;
  updateTenantCluster: AstroliftTenantClusterMutationResult;
  updateToolDef: AstroliftToolDefMutationResult;
  updateWebhookSubscription: AstroliftWebhookSubscriptionMutationResult;
  /** Update a configured Workflow (bindings / inputs / trigger / enabled). definitionSlug repoints it at another visible definition: the fallback for a versioned importWorkflowManifest(replace: true) (#1822), or any manual repoint. The existing stage_bindings must still validate against the new definition's stages, or the update is refused. */
  updateWorkflow: CreateWorkflowResult;
  /** Update an org-owned workflow definition (globals are read-only). */
  updateWorkflowDefinition: MutationResult;
  /** Update a stage's fields on a writable definition (spec 40 §6). */
  updateWorkflowStage: MutationResult;
  uploadCustomDomainCertificate: AstroliftAppDomainMutationResult;
  /** Upload a data import file and get a pre-signed URL. */
  uploadTextFile: DataImportUploadResult;
  upsertAgentSecretRef: AstroliftAgentSecretStatusMutationResult;
  upsertOrganization: OrganizationMutationResult;
  /** Upsert user profile via UtilityForm.apply_forms. */
  upsertUser: UpsertUserResult;
  validateAstroliftCiSecrets: AstroliftValidateCiSecretsPayloadMutationResult;
  verifyManagedDomain: VerifyManagedDomainPayloadMutationResult;
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
  active?: Scalars["Boolean"]["input"];
  gid: Scalars["ID"]["input"];
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

export type MutationAdoptManagedResourceArgs = {
  input: AdoptManagedResourceInput;
};

export type MutationAdoptRepoCiWorkflowArgs = {
  input: CiWorkflowSyncActionInput;
};

export type MutationApplyStagedManifestArgs = {
  input: ApplyStagedManifestInput;
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
  slug: Scalars["String"]["input"];
};

export type MutationAssembleBriefArgs = {
  config?: InputMaybe<Scalars["JSON"]["input"]>;
  orgId: Scalars["ID"]["input"];
  skillIds: Array<Scalars["ID"]["input"]>;
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

export type MutationAttachAgentSecretBundleArgs = {
  bundleId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
  environment?: Scalars["String"]["input"];
  position?: Scalars["Int"]["input"];
  prefix?: Scalars["String"]["input"];
};

export type MutationAttachProjectManagedServiceArgs = {
  input: AttachProjectManagedServiceInput;
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
  confirmed?: Scalars["Boolean"]["input"];
  expectedVersion?: InputMaybe<Scalars["Int"]["input"]>;
  runId: Scalars["GUID"]["input"];
  temporalRunId?: InputMaybe<Scalars["String"]["input"]>;
  temporalWorkflowId?: InputMaybe<Scalars["String"]["input"]>;
};

export type MutationCancelTaskArgs = {
  id: Scalars["ID"]["input"];
};

export type MutationCancelWorkflowInstanceArgs = {
  workflowId: Scalars["String"]["input"];
};

export type MutationClearAgentQuarantineArgs = {
  id: Scalars["ID"]["input"];
};

export type MutationClearAlertSubscriptionArgs = {
  input: ClearAlertSubscriptionInput;
};

export type MutationClearEnvironmentSettingArgs = {
  input: ClearEnvironmentSettingInput;
};

export type MutationCloneWorkflowDefinitionArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type MutationConfigureAgentTaskCallbacksArgs = {
  allowedHosts: Array<Scalars["String"]["input"]>;
};

export type MutationConfigureProviderPluginArgs = {
  input: ConfigureProviderPluginInput;
};

export type MutationConfirmPreSignedUrlImageUploadArgs = {
  delete?: InputMaybe<Scalars["Boolean"]["input"]>;
  expirationDate?: InputMaybe<Scalars["Date"]["input"]>;
  metadata?: InputMaybe<Scalars["JSON"]["input"]>;
  publicUrl?: InputMaybe<Scalars["String"]["input"]>;
  uploadId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type MutationConnectExistingGithubAppArgs = {
  input: ConnectExistingGithubAppInput;
};

export type MutationConnectSourceArgs = {
  input: ConnectSourceInput;
};

export type MutationConnectZentinelleArgs = {
  input: ConnectZentinelleInput;
};

export type MutationControlWorkflowExecutionArgs = {
  action: Scalars["String"]["input"];
  executionId: Scalars["ID"]["input"];
  reason?: Scalars["String"]["input"];
  runId: Scalars["String"]["input"];
  workflowId: Scalars["String"]["input"];
};

export type MutationCreateAgentEnvironmentSpecArgs = {
  input: CreateAgentEnvironmentSpecInput;
  orgId: Scalars["ID"]["input"];
};

export type MutationCreateAgentSecretBundleArgs = {
  backendRef?: Scalars["String"]["input"];
  envSpecSlug: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
};

export type MutationCreateAgentTriggerArgs = {
  agentSlug: Scalars["String"]["input"];
  branchPattern?: Scalars["String"]["input"];
  inputMapping?: InputMaybe<Scalars["JSON"]["input"]>;
  scmRepo?: Scalars["String"]["input"];
};

export type MutationCreateAlertRuleArgs = {
  input: CreateAlertRuleInput;
};

export type MutationCreateApiTokenArgs = {
  input: CreateApiTokenInput;
};

export type MutationCreateClusterAuthGroupArgs = {
  input: CreateClusterAuthGroupInput;
};

export type MutationCreateClusterAuthUserArgs = {
  input: CreateClusterAuthUserInput;
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

export type MutationCreateGroupRoleMappingArgs = {
  input: CreateGroupRoleMappingInput;
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

export type MutationCreateProjectSecretBundleArgs = {
  input: CreateProjectSecretBundleInput;
};

export type MutationCreateRoleArgs = {
  input: CreateRoleInput;
};

export type MutationCreateSkillArgs = {
  input: SkillInput;
  orgId: Scalars["ID"]["input"];
};

export type MutationCreateTeamArgs = {
  input: CreateTeamInput;
};

export type MutationCreateToolDefArgs = {
  input: ToolDefInput;
  skillId: Scalars["ID"]["input"];
};

export type MutationCreateTriggerArgs = {
  input: CreateTriggerInput;
};

export type MutationCreateWebhookSubscriptionArgs = {
  input: CreateWebhookSubscriptionInput;
};

export type MutationCreateWorkflowArgs = {
  definitionSlug: Scalars["String"]["input"];
  description?: InputMaybe<Scalars["String"]["input"]>;
  inputs?: InputMaybe<Scalars["JSON"]["input"]>;
  name: Scalars["String"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  scheduleCron?: InputMaybe<Scalars["String"]["input"]>;
  slug?: InputMaybe<Scalars["String"]["input"]>;
  stageBindings?: InputMaybe<Scalars["JSON"]["input"]>;
  triggerKind?: Scalars["String"]["input"];
};

export type MutationCreateWorkflowDefinitionArgs = {
  description?: InputMaybe<Scalars["String"]["input"]>;
  inputSchema?: InputMaybe<Scalars["JSON"]["input"]>;
  isEnabled?: Scalars["Boolean"]["input"];
  modelLabel: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
  patternKind?: InputMaybe<Scalars["String"]["input"]>;
  slug: Scalars["String"]["input"];
  states: Scalars["JSON"]["input"];
  transitions: Scalars["JSON"]["input"];
};

export type MutationCreateWorkflowStageArgs = {
  agentDefinitionGuid?: InputMaybe<Scalars["String"]["input"]>;
  agentRef?: InputMaybe<Scalars["String"]["input"]>;
  approvers?: InputMaybe<Scalars["JSON"]["input"]>;
  environmentSpecSlug?: InputMaybe<Scalars["String"]["input"]>;
  fanOutCount?: InputMaybe<Scalars["Int"]["input"]>;
  kind: Scalars["String"]["input"];
  onFailure?: Scalars["String"]["input"];
  order?: InputMaybe<Scalars["Int"]["input"]>;
  outputKey?: InputMaybe<Scalars["String"]["input"]>;
  prompt?: InputMaybe<Scalars["String"]["input"]>;
  role?: InputMaybe<Scalars["String"]["input"]>;
  skillRefs?: InputMaybe<Scalars["JSON"]["input"]>;
  timeoutSeconds?: Scalars["Int"]["input"];
  workflowRef?: InputMaybe<Scalars["String"]["input"]>;
  workflowSlug: Scalars["String"]["input"];
};

export type MutationCreateWorkflowTriggerArgs = {
  workflowSlug: Scalars["String"]["input"];
};

export type MutationDecommissionClusterArgs = {
  input: DecommissionClusterInputType;
};

export type MutationDeleteArgs = {
  gid: Scalars["ID"]["input"];
};

export type MutationDeleteAgentBundleSecretValueArgs = {
  bundleId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
  key: Scalars["String"]["input"];
};

export type MutationDeleteAgentEnvironmentSpecArgs = {
  slug: Scalars["String"]["input"];
};

export type MutationDeleteAgentSecretBundleArgs = {
  bundleId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
};

export type MutationDeleteAgentSecretValueArgs = {
  envSpecSlug: Scalars["String"]["input"];
  envVar: Scalars["String"]["input"];
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

export type MutationDeleteClusterAuthUserArgs = {
  input: ClusterAuthUserRefInput;
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

export type MutationDeleteGroupRoleMappingArgs = {
  input: DeleteGroupRoleMappingInput;
};

export type MutationDeleteInvitationArgs = {
  input: RevokeInvitationInput;
};

export type MutationDeletePipelineArgs = {
  id: Scalars["GUID"]["input"];
};

export type MutationDeletePipelineSecretArgs = {
  input: DeletePipelineSecretInput;
};

export type MutationDeleteProjectBundleSecretValueArgs = {
  input: ProjectSecretBundleKeyInput;
};

export type MutationDeleteProjectSecretBundleArgs = {
  bundleId: Scalars["GUID"]["input"];
};

export type MutationDeleteSkillArgs = {
  id: Scalars["ID"]["input"];
};

export type MutationDeleteSshDeployKeyArgs = {
  input: DeleteSshDeployKeyInput;
};

export type MutationDeleteToolDefArgs = {
  id: Scalars["ID"]["input"];
};

export type MutationDeleteWebhookSubscriptionArgs = {
  input: DeleteWebhookSubscriptionInput;
};

export type MutationDeleteWorkflowArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type MutationDeleteWorkflowDefinitionArgs = {
  slug: Scalars["String"]["input"];
};

export type MutationDeleteWorkflowStageArgs = {
  stageGuid: Scalars["ID"]["input"];
};

export type MutationDeployClusterAgentArgs = {
  input: DeployClusterAgentInput;
};

export type MutationDeprovisionClusterModelArgs = {
  input: DeprovisionClusterModelInput;
};

export type MutationDeprovisionManagedServiceArgs = {
  input: DeprovisionManagedServiceInput;
};

export type MutationDeprovisionProjectManagedServiceArgs = {
  input: DeprovisionManagedServiceInput;
};

export type MutationDeregisterAstroliftAppArgs = {
  input: DeregisterAppInput;
};

export type MutationDestroyAgentBoxArgs = {
  slug: Scalars["String"]["input"];
};

export type MutationDetachAgentSecretBundleArgs = {
  attachmentId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
};

export type MutationDetachProjectManagedServiceArgs = {
  input: DetachProjectManagedServiceInput;
};

export type MutationDetachSecretBundleArgs = {
  input: DetachSecretBundleInput;
};

export type MutationDisconnectSourceArgs = {
  input: DisconnectSourceInput;
};

export type MutationDisconnectZentinelleArgs = {
  input: DisconnectZentinelleInput;
};

export type MutationElevateAdminSessionArgs = {
  input: ElevateAdminSessionInput;
};

export type MutationEnsureAgentBoxArgs = {
  input: EnsureAgentBoxInput;
  orgId: Scalars["ID"]["input"];
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
  description?: InputMaybe<Scalars["String"]["input"]>;
  fileUploadGid?: InputMaybe<Scalars["ID"]["input"]>;
  isPublic?: InputMaybe<Scalars["Boolean"]["input"]>;
  metadata?: InputMaybe<Scalars["JSON"]["input"]>;
  mimetype: Scalars["String"]["input"];
  name?: InputMaybe<Scalars["String"]["input"]>;
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
  branch?: Scalars["String"]["input"];
  manifestPath?: Scalars["String"]["input"];
  repoUrl: Scalars["String"]["input"];
};

export type MutationImportWorkflowFlowArgs = {
  format: Scalars["String"]["input"];
  payload: Scalars["JSON"]["input"];
  preview?: Scalars["Boolean"]["input"];
};

export type MutationImportWorkflowManifestArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  preview?: Scalars["Boolean"]["input"];
  replace?: Scalars["Boolean"]["input"];
  toml: Scalars["String"]["input"];
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
  briefId: Scalars["ID"]["input"];
  callbackUrl?: InputMaybe<Scalars["String"]["input"]>;
  orgId: Scalars["ID"]["input"];
};

export type MutationLibraryMkdirArgs = {
  icon?: InputMaybe<Scalars["ID"]["input"]>;
  name: Scalars["String"]["input"];
  parentGuid?: InputMaybe<Scalars["ID"]["input"]>;
};

export type MutationLibraryRenameDirArgs = {
  guid: Scalars["ID"]["input"];
  name: Scalars["String"]["input"];
};

export type MutationLibraryRenameFileArgs = {
  guid: Scalars["ID"]["input"];
  name: Scalars["String"]["input"];
};

export type MutationLibraryRmFileArgs = {
  directory: Scalars["ID"]["input"];
  file: Scalars["ID"]["input"];
};

export type MutationLibraryRmdirArgs = {
  directoryGuid: Scalars["ID"]["input"];
};

export type MutationLibrarySetIconArgs = {
  directory: Scalars["ID"]["input"];
  file: Scalars["ID"]["input"];
};

export type MutationLoginArgs = {
  password: Scalars["String"]["input"];
  username: Scalars["String"]["input"];
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
  input: Scalars["JSON"]["input"];
};

export type MutationNotificationReadArgs = {
  gid: Scalars["ID"]["input"];
};

export type MutationOpenCiWorkflowReconcilePrArgs = {
  input: CiWorkflowSyncActionInput;
};

export type MutationOrganizationArgs = {
  input: OrganizationInput;
};

export type MutationOrganizationMemberStatusArgs = {
  input: OrganizationMemberStatusInput;
};

export type MutationOverrideWorkflowStateArgs = {
  instanceId: Scalars["ID"]["input"];
  note?: Scalars["String"]["input"];
  toState: Scalars["String"]["input"];
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
  pin: Scalars["String"]["input"];
  proxyUser?: InputMaybe<Scalars["ID"]["input"]>;
};

export type MutationPinUpdateArgs = {
  pin: Scalars["String"]["input"];
};

export type MutationPlaceObservabilityRetentionHoldArgs = {
  input: PlaceObservabilityRetentionHoldInput;
};

export type MutationPreSignedUrlImageUploadArgs = {
  description?: InputMaybe<Scalars["String"]["input"]>;
  globalId: Scalars["ID"]["input"];
  location?: InputMaybe<UploadLocation>;
  metadata?: InputMaybe<Scalars["JSON"]["input"]>;
  mimetype: Scalars["String"]["input"];
  name?: InputMaybe<Scalars["String"]["input"]>;
  ownerContainerProperty?: InputMaybe<Scalars["String"]["input"]>;
  uuid?: InputMaybe<Scalars["UUID"]["input"]>;
};

export type MutationProcessFileArgs = {
  entityType: EntityType;
  processId?: InputMaybe<Scalars["ID"]["input"]>;
  uploadedFileId: Scalars["ID"]["input"];
};

export type MutationProfileArgs = {
  input: Scalars["JSON"]["input"];
};

export type MutationProfileImageFieldUploadArgs = {
  field?: InputMaybe<ProfileImageField>;
  globalId: Scalars["ID"]["input"];
  metadata?: InputMaybe<Scalars["JSON"]["input"]>;
  mimetype: Scalars["String"]["input"];
};

export type MutationProfileRequestDeleteUserArgs = {
  userGid?: InputMaybe<Scalars["ID"]["input"]>;
};

export type MutationProfileRequestPwdChangeArgs = {
  userGid?: InputMaybe<Scalars["ID"]["input"]>;
};

export type MutationPromoteDeploymentArgs = {
  input: PromoteDeploymentInput;
};

export type MutationProposeSecretChangeArgs = {
  input: ProposeSecretChangeInput;
};

export type MutationProvisionClusterModelArgs = {
  input: ProvisionClusterModelInput;
};

export type MutationProvisionManagedDomainArgs = {
  clusterId: Scalars["GUID"]["input"];
  isPlatformManagedZone?: Scalars["Boolean"]["input"];
  zone: Scalars["String"]["input"];
};

export type MutationProvisionManagedServiceArgs = {
  input: ProvisionManagedServiceInput;
};

export type MutationProvisionProjectManagedServiceArgs = {
  input: ProvisionProjectManagedServiceInput;
};

export type MutationPublishFormArgs = {
  slug: Scalars["String"]["input"];
};

export type MutationPullCiWorkflowFromRepoArgs = {
  input: CiWorkflowSyncActionInput;
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

export type MutationReapCloudOrphanArgs = {
  input: ReapCloudOrphanInput;
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

export type MutationRedeliverAgentTaskCallbackArgs = {
  taskId: Scalars["GUID"]["input"];
};

export type MutationRedeployAppArgs = {
  ifMatchVersion?: InputMaybe<Scalars["Int"]["input"]>;
  input: DeploymentByIdInput;
};

export type MutationRefreshCiWorkflowSyncStatusArgs = {
  input: CiWorkflowSyncActionInput;
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

export type MutationRegisterAppRepoArgs = {
  input: RegisterAppRepoInput;
};

export type MutationRegisterMobileDeviceArgs = {
  input: RegisterMobileDeviceInput;
};

export type MutationRegisterOrgSkillRepoArgs = {
  input: RegisterOrgSkillRepoInput;
  orgId: Scalars["ID"]["input"];
};

export type MutationRegisterTenantClusterArgs = {
  input: RegisterTenantClusterInput;
};

export type MutationRegisterZentinelleClusterArgs = {
  input: ZentinelleClusterInput;
};

export type MutationReissueManagedDomainCertArgs = {
  clusterId: Scalars["GUID"]["input"];
  zone: Scalars["String"]["input"];
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

export type MutationReleaseObservabilityRetentionHoldArgs = {
  input: ReleaseObservabilityRetentionHoldInput;
};

export type MutationRemoveAgentSecretRefArgs = {
  envSpecSlug: Scalars["String"]["input"];
  envVar: Scalars["String"]["input"];
};

export type MutationRemoveAppDomainArgs = {
  input: RemoveAppDomainInput;
};

export type MutationRemoveEmailSuppressionEntryArgs = {
  input: RemoveEmailSuppressionEntryInput;
};

export type MutationRemoveOrgSkillRepoArgs = {
  input: RemoveOrgSkillRepoInput;
};

export type MutationRemoveOrganizationAllowlistDomainArgs = {
  input: RemoveOrganizationAllowlistDomainInput;
};

export type MutationReorderWorkflowStagesArgs = {
  definitionSlug: Scalars["String"]["input"];
  stageGuids: Array<Scalars["ID"]["input"]>;
};

export type MutationReplyAgentTaskInputArgs = {
  requestSequence: Scalars["Int"]["input"];
  response: Scalars["JSON"]["input"];
  taskId: Scalars["ID"]["input"];
};

export type MutationReprovisionManagedServiceArgs = {
  input: ReprovisionManagedServiceInput;
};

export type MutationReprovisionProjectManagedServiceArgs = {
  input: ReprovisionManagedServiceInput;
};

export type MutationRequestAttestationChallengeArgs = {
  input: RequestAttestationChallengeInput;
};

export type MutationRequestQuotaIncreaseArgs = {
  input: RequestQuotaIncreaseInput;
};

export type MutationRerunAstroliftOnboardingArgs = {
  input: RerunOnboardingInput;
};

export type MutationResendInvitationArgs = {
  input: ResendInvitationInput;
};

export type MutationResetClusterAuthUserPasswordArgs = {
  input: ClusterAuthUserRefInput;
};

export type MutationRestartAstroliftWorkloadArgs = {
  ifMatchVersion?: InputMaybe<Scalars["Int"]["input"]>;
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

export type MutationResyncAstroliftCiWorkflowArgs = {
  input: CiWorkflowSyncActionInput;
};

export type MutationResyncAstroliftManifestFromRepoArgs = {
  input: ResyncManifestFromRepoInput;
};

export type MutationRetryAgentTaskArgs = {
  id: Scalars["ID"]["input"];
};

export type MutationRetryAstroliftAutowireArgs = {
  input: RetryAstroliftAutowireInput;
};

export type MutationRevalidateManagedDomainArgs = {
  clusterId: Scalars["GUID"]["input"];
  zone: Scalars["String"]["input"];
};

export type MutationRevealAgentBundleSecretValueArgs = {
  bundleId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
  key: Scalars["String"]["input"];
};

export type MutationRevealAgentSecretValueArgs = {
  envSpecSlug: Scalars["String"]["input"];
  envVar: Scalars["String"]["input"];
};

export type MutationRevealAppSecretArgs = {
  input: RevealAppSecretInput;
};

export type MutationRevealManagedServiceConnectionArgs = {
  input: RevealManagedServiceConnectionInput;
};

export type MutationRevealProjectBundleSecretValueArgs = {
  input: ProjectSecretBundleKeyInput;
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

export type MutationRevokeModelSubscriptionArgs = {
  input: RevokeModelSubscriptionInput;
};

export type MutationRevokeRoleBindingArgs = {
  input: RevokeRoleBindingInput;
};

export type MutationRevokeTeamAccessFromAppArgs = {
  input: RevokeTeamAccessInput;
};

export type MutationRollbackDeploymentArgs = {
  ifMatchVersion?: InputMaybe<Scalars["Int"]["input"]>;
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

export type MutationRotateZentinelleGatewayCredentialArgs = {
  input: RotateZentinelleGatewayCredentialInput;
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

export type MutationRunWorkflowArgs = {
  inputs?: InputMaybe<Scalars["JSON"]["input"]>;
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  workflowId: Scalars["ID"]["input"];
};

export type MutationRunWorkflowDefinitionArgs = {
  confirmed?: InputMaybe<Scalars["Boolean"]["input"]>;
  definitionId?: InputMaybe<Scalars["GUID"]["input"]>;
  expectedInputSchemaDigest?: InputMaybe<Scalars["String"]["input"]>;
  expectedRevision?: InputMaybe<Scalars["String"]["input"]>;
  requestId?: InputMaybe<Scalars["String"]["input"]>;
  triggerPayload?: InputMaybe<Scalars["JSON"]["input"]>;
  workflowSlug: Scalars["String"]["input"];
};

export type MutationScaleAstroliftWorkloadArgs = {
  ifMatchVersion?: InputMaybe<Scalars["Int"]["input"]>;
  input: ScaleWorkloadInput;
};

export type MutationScaleServiceAgentArgs = {
  agentSlug: Scalars["String"]["input"];
  targetReplicas: Scalars["Int"]["input"];
};

export type MutationSendAgentTaskInputArgs = {
  clientRequestId?: InputMaybe<Scalars["String"]["input"]>;
  message: Scalars["String"]["input"];
  taskId: Scalars["ID"]["input"];
};

export type MutationSendManagedServiceTestEmailArgs = {
  input: SendManagedServiceTestEmailInput;
};

export type MutationSetActiveIdentityProviderArgs = {
  input: SetActiveIdentityProviderInput;
};

export type MutationSetAgentBundleSecretValueArgs = {
  bundleId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
  key: Scalars["String"]["input"];
  value: Scalars["String"]["input"];
};

export type MutationSetAgentSecretValueArgs = {
  envSpecSlug: Scalars["String"]["input"];
  envVar: Scalars["String"]["input"];
  value: Scalars["String"]["input"];
};

export type MutationSetAgentTaskCallbackSecretArgs = {
  name: Scalars["String"]["input"];
  value: Scalars["String"]["input"];
};

export type MutationSetAlertSubscriptionArgs = {
  input: SetAlertSubscriptionInput;
};

export type MutationSetAppAccessArgs = {
  input: SetAppAccessInput;
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

export type MutationSetClusterAuthUserEnabledArgs = {
  input: SetClusterAuthUserEnabledInput;
};

export type MutationSetClusterAuthUserGroupsArgs = {
  input: SetClusterAuthUserGroupsInput;
};

export type MutationSetClusterAuthUserPasswordArgs = {
  input: SetClusterAuthUserPasswordInput;
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

export type MutationSetFeatureFlagArgs = {
  enabled: Scalars["Boolean"]["input"];
  key: Scalars["String"]["input"];
};

export type MutationSetNotificationPreferenceArgs = {
  input: SetNotificationPreferenceInput;
};

export type MutationSetNotificationProfileArgs = {
  input: SetNotificationProfileInput;
};

export type MutationSetOrganizationModuleArgs = {
  input: SetOrganizationModuleInput;
};

export type MutationSetPipelineSecretArgs = {
  input: SetPipelineSecretInput;
};

export type MutationSetPreviewPinnedArgs = {
  input: SetPreviewPinnedInput;
};

export type MutationSetProjectBundleSecretValueArgs = {
  input: ProjectSecretBundleKeyInput;
};

export type MutationSetRetentionPolicyArgs = {
  input: SetRetentionPolicyInput;
};

export type MutationSetZentinelleGatewayEnabledArgs = {
  input: SetZentinelleGatewayEnabledInput;
};

export type MutationSignRequestCancelArgs = {
  gid: Scalars["String"]["input"];
  note: Scalars["String"]["input"];
};

export type MutationSignRequestSignArgs = {
  gid: Scalars["String"]["input"];
};

export type MutationSignRequestUserArgs = {
  gid: Scalars["String"]["input"];
  userToRequest: Scalars["ID"]["input"];
};

export type MutationSignalWorkflowInstanceArgs = {
  payload?: InputMaybe<Scalars["JSON"]["input"]>;
  signalName: Scalars["String"]["input"];
  workflowId: Scalars["String"]["input"];
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

export type MutationStartPipelineRunArgs = {
  input: StartPipelineRunInput;
};

export type MutationStartWorkflowArgs = {
  modelLabel: Scalars["String"]["input"];
  objectId: Scalars["Int"]["input"];
  workflowSlug: Scalars["String"]["input"];
};

export type MutationStartWorkflowDefinitionArgs = {
  input: StartWorkflowDefinitionInput;
};

export type MutationSubmitFormArgs = {
  payload: Scalars["JSON"]["input"];
  slug: Scalars["String"]["input"];
};

export type MutationSubscribeClusterModelArgs = {
  input: SubscribeClusterModelInput;
};

export type MutationSwitchUserArgs = {
  id: Scalars["ID"]["input"];
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
  reason: Scalars["String"]["input"];
  workflowId: Scalars["String"]["input"];
};

export type MutationTestModelEndpointArgs = {
  input: TestModelEndpointInput;
};

export type MutationTestNotificationChannelArgs = {
  input: TestNotificationInput;
};

export type MutationTestSharedModelEndpointArgs = {
  input: TestSharedModelEndpointInput;
};

export type MutationTestWebhookSubscriptionArgs = {
  input: TestWebhookInput;
};

export type MutationTransferAppArgs = {
  input: TransferAppInput;
};

export type MutationTransitionWorkflowArgs = {
  instanceId: Scalars["ID"]["input"];
  note?: Scalars["String"]["input"];
  toState: Scalars["String"]["input"];
};

export type MutationTriggerAstroliftDeployWorkflowArgs = {
  input: TriggerDeployWorkflowInput;
};

export type MutationTriggerPipelineRunArgs = {
  confirmed?: Scalars["Boolean"]["input"];
  expectedVersion?: InputMaybe<Scalars["Int"]["input"]>;
  pipelineId: Scalars["GUID"]["input"];
  ref?: InputMaybe<Scalars["String"]["input"]>;
  requestId?: InputMaybe<Scalars["String"]["input"]>;
};

export type MutationUnbindAgentTriggerArgs = {
  slug: Scalars["String"]["input"];
};

export type MutationUnmuteAlertRuleArgs = {
  input: UnmuteAlertRuleInput;
};

export type MutationUnregisterTenantClusterArgs = {
  input: UnregisterTenantClusterInput;
};

export type MutationUnregisterZentinelleClusterArgs = {
  input: ZentinelleClusterInput;
};

export type MutationUpdateAgentEnvironmentSpecArgs = {
  input: UpdateAgentEnvironmentSpecInput;
  slug: Scalars["String"]["input"];
};

export type MutationUpdateAgentRunSpecArgs = {
  agentSlug: Scalars["String"]["input"];
  input: AgentRunSpecInput;
};

export type MutationUpdateAgentSecretBundleArgs = {
  backendRef: Scalars["String"]["input"];
  bundleId: Scalars["ID"]["input"];
  envSpecSlug: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
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

export type MutationUpdateClusterModelArgs = {
  input: UpdateClusterModelInput;
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

export type MutationUpdateMyUiPreferencesArgs = {
  input: UpdateMyUiPreferencesInput;
};

export type MutationUpdateOrgSkillRepoArgs = {
  input: UpdateOrgSkillRepoInput;
};

export type MutationUpdateOrganizationArgs = {
  input: UpdateOrganizationInput;
};

export type MutationUpdatePipelineArgs = {
  id: Scalars["GUID"]["input"];
  input: UpdatePipelineInput;
};

export type MutationUpdatePolicyArgs = {
  input: UpdatePolicyInput;
};

export type MutationUpdateProjectArgs = {
  input: UpdateProjectInput;
};

export type MutationUpdateProjectManagedServiceArgs = {
  input: UpdateManagedServiceInput;
};

export type MutationUpdateProjectSecretBundleArgs = {
  input: UpdateProjectSecretBundleInput;
};

export type MutationUpdateRoleArgs = {
  input: UpdateRoleInput;
};

export type MutationUpdateRoleBindingArgs = {
  input: UpdateRoleBindingInput;
};

export type MutationUpdateSkillArgs = {
  id: Scalars["ID"]["input"];
  input: SkillInput;
};

export type MutationUpdateSourceConnectionArgs = {
  input: UpdateSourceConnectionInput;
};

export type MutationUpdateSubmissionStatusArgs = {
  status: Scalars["String"]["input"];
  submissionId: Scalars["GUID"]["input"];
};

export type MutationUpdateTeamArgs = {
  input: UpdateTeamInput;
};

export type MutationUpdateTenantClusterArgs = {
  input: UpdateTenantClusterInput;
};

export type MutationUpdateToolDefArgs = {
  id: Scalars["ID"]["input"];
  input: ToolDefInput;
};

export type MutationUpdateWebhookSubscriptionArgs = {
  input: UpdateWebhookSubscriptionInput;
};

export type MutationUpdateWorkflowArgs = {
  definitionSlug?: InputMaybe<Scalars["String"]["input"]>;
  description?: InputMaybe<Scalars["String"]["input"]>;
  inputs?: InputMaybe<Scalars["JSON"]["input"]>;
  isEnabled?: InputMaybe<Scalars["Boolean"]["input"]>;
  name?: InputMaybe<Scalars["String"]["input"]>;
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  scheduleCron?: InputMaybe<Scalars["String"]["input"]>;
  slug: Scalars["String"]["input"];
  stageBindings?: InputMaybe<Scalars["JSON"]["input"]>;
  triggerKind?: InputMaybe<Scalars["String"]["input"]>;
};

export type MutationUpdateWorkflowDefinitionArgs = {
  description?: InputMaybe<Scalars["String"]["input"]>;
  inputSchema?: InputMaybe<Scalars["JSON"]["input"]>;
  isEnabled?: InputMaybe<Scalars["Boolean"]["input"]>;
  modelLabel?: InputMaybe<Scalars["String"]["input"]>;
  name?: InputMaybe<Scalars["String"]["input"]>;
  patternKind?: InputMaybe<Scalars["String"]["input"]>;
  slug: Scalars["String"]["input"];
  states?: InputMaybe<Scalars["JSON"]["input"]>;
  transitions?: InputMaybe<Scalars["JSON"]["input"]>;
};

export type MutationUpdateWorkflowStageArgs = {
  agentDefinitionGuid?: InputMaybe<Scalars["String"]["input"]>;
  agentRef?: InputMaybe<Scalars["String"]["input"]>;
  approvers?: InputMaybe<Scalars["JSON"]["input"]>;
  environmentSpecSlug?: InputMaybe<Scalars["String"]["input"]>;
  fanOutCount?: InputMaybe<Scalars["Int"]["input"]>;
  kind?: InputMaybe<Scalars["String"]["input"]>;
  onFailure?: InputMaybe<Scalars["String"]["input"]>;
  outputKey?: InputMaybe<Scalars["String"]["input"]>;
  prompt?: InputMaybe<Scalars["String"]["input"]>;
  role?: InputMaybe<Scalars["String"]["input"]>;
  skillRefs?: InputMaybe<Scalars["JSON"]["input"]>;
  stageGuid: Scalars["ID"]["input"];
  timeoutSeconds?: InputMaybe<Scalars["Int"]["input"]>;
  workflowRef?: InputMaybe<Scalars["String"]["input"]>;
};

export type MutationUploadCustomDomainCertificateArgs = {
  input: UploadCustomDomainCertificateInput;
};

export type MutationUploadTextFileArgs = {
  metadata?: InputMaybe<Scalars["JSON"]["input"]>;
  mimetype: Scalars["String"]["input"];
};

export type MutationUpsertAgentSecretRefArgs = {
  envSpecSlug: Scalars["String"]["input"];
  envVar: Scalars["String"]["input"];
  uri: Scalars["String"]["input"];
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

export type MutationVerifyManagedDomainArgs = {
  input: VerifyManagedDomainInput;
};

export type MutationWithdrawSecretChangeArgs = {
  input: WithdrawSecretChangeInput;
};

export type MutationError = {
  code: Scalars["String"]["output"];
  currentVersion?: Maybe<Scalars["Int"]["output"]>;
  field?: Maybe<Scalars["String"]["output"]>;
  message: Scalars["String"]["output"];
  requestedVersion?: Maybe<Scalars["Int"]["output"]>;
  requiresAttestation?: Maybe<Scalars["Boolean"]["output"]>;
  supportedMethods?: Maybe<Array<Scalars["String"]["output"]>>;
};

/** Standard mutation result with ok flag and validation errors. */
export type MutationResult = {
  errors: Array<ValidationError>;
  ok: Scalars["Boolean"]["output"];
};

export type MuteAlertRuleInput = {
  durationSeconds: Scalars["Int"]["input"];
  reason: Scalars["String"]["input"];
  ruleId: Scalars["GUID"]["input"];
};

export type NoneTypeMutationResult = {
  data?: Maybe<Scalars["Void"]["output"]>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ObservabilityRetentionHoldType = {
  endsAt: Scalars["DateTime"]["output"];
  id: Scalars["GUID"]["output"];
  reason: Scalars["String"]["output"];
  released: Scalars["Boolean"]["output"];
  resourceId: Scalars["String"]["output"];
  resourceKind: Scalars["String"]["output"];
  startsAt: Scalars["DateTime"]["output"];
  stream: Scalars["String"]["output"];
};

export type ObservabilityRetentionHoldTypeMutationResult = {
  data?: Maybe<ObservabilityRetentionHoldType>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ObservabilityRetentionType = {
  billableWindowDays: Scalars["Int"]["output"];
  days: Scalars["Int"]["output"];
  source: Scalars["String"]["output"];
  stream: Scalars["String"]["output"];
  warnThresholdDays: Scalars["Int"]["output"];
};

export type OrganizationInput = {
  id: InputMaybe<Scalars["ID"]["input"]>;
  website: InputMaybe<Scalars["String"]["input"]>;
};

export type OrganizationMemberStatusInput = {
  isActive: Scalars["Boolean"]["input"];
  organizationId: InputMaybe<Scalars["ID"]["input"]>;
  userId: Scalars["ID"]["input"];
};

export type OrganizationMemberType = {
  createdAt: Scalars["DateTime"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  isActive: Scalars["Boolean"]["output"];
  member?: Maybe<Scalars["JSON"]["output"]>;
  organization?: Maybe<OrganizationType>;
  updatedAt: Scalars["DateTime"]["output"];
  version: Scalars["Int"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type OrganizationMemberTypePage = {
  items: Array<OrganizationMemberType>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type OrganizationMutationResult = {
  errors: Array<ValidationError>;
  id?: Maybe<Scalars["ID"]["output"]>;
  ok: Scalars["Boolean"]["output"];
};

export type OrganizationType = {
  createdAt: Scalars["DateTime"]["output"];
  deletedAt?: Maybe<Scalars["DateTime"]["output"]>;
  description?: Maybe<Scalars["String"]["output"]>;
  guid: Scalars["UUID"]["output"];
  name: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  updatedAt: Scalars["DateTime"]["output"];
  version: Scalars["Int"]["output"];
  website?: Maybe<Scalars["String"]["output"]>;
};

/** One page of a cursor-paginated or numbered list. */
export type OrganizationTypePage = {
  items: Array<OrganizationType>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type PauseAppWebhookDeploysInput = {
  appSlug: Scalars["String"]["input"];
  reason: InputMaybe<Scalars["String"]["input"]>;
};

export type PendingHumanGate = {
  definitionName: Scalars["String"]["output"];
  definitionSlug: Scalars["String"]["output"];
  executionId: Scalars["String"]["output"];
  runGuid: Scalars["String"]["output"];
  stageApprovers: Array<Scalars["String"]["output"]>;
  stageRole: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  workflowId: Scalars["String"]["output"];
};

export type PermissionComparison = {
  differences: Array<PermissionDiff>;
  onlyA: Array<Scalars["String"]["output"]>;
  onlyB: Array<Scalars["String"]["output"]>;
  shared: Array<Scalars["String"]["output"]>;
  userAUsername: Scalars["String"]["output"];
  userBUsername: Scalars["String"]["output"];
};

export type PermissionDiagnosis = {
  granted: Scalars["Boolean"]["output"];
  isSuperuser: Scalars["Boolean"]["output"];
  permission: Scalars["String"]["output"];
  steps: Array<PermissionTraceStep>;
  userId: Scalars["ID"]["output"];
  username: Scalars["String"]["output"];
};

export type PermissionDiff = {
  slug: Scalars["String"]["output"];
  userAHas: Scalars["Boolean"]["output"];
  userBHas: Scalars["Boolean"]["output"];
};

export type PermissionEntry = {
  action: Scalars["String"]["output"];
  grantedVia: Array<Scalars["String"]["output"]>;
  resource: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type PermissionTraceStep = {
  check: Scalars["String"]["output"];
  detail: Scalars["String"]["output"];
  result: Scalars["Boolean"]["output"];
};

export type PlaceObservabilityRetentionHoldInput = {
  endsAt: Scalars["DateTime"]["input"];
  reason: Scalars["String"]["input"];
  resourceId: Scalars["String"]["input"];
  resourceKind: Scalars["String"]["input"];
  startsAt: Scalars["DateTime"]["input"];
  stream: Scalars["String"]["input"];
};

export type PreSignedUrlUploadResult = {
  fileUrl?: Maybe<Scalars["String"]["output"]>;
  preSignedUrl?: Maybe<Scalars["String"]["output"]>;
  publicUrl?: Maybe<Scalars["String"]["output"]>;
  uuid?: Maybe<Scalars["UUID"]["output"]>;
};

export type ProcessFileResult = {
  errors?: Maybe<Scalars["Int"]["output"]>;
  processId?: Maybe<Scalars["ID"]["output"]>;
  status?: Maybe<Scalars["String"]["output"]>;
  successful?: Maybe<Scalars["Int"]["output"]>;
};

export type ProfileImageField = "AVATAR" | "SIGNATURE";

export type ProfileImageFieldUploadResult = {
  upload?: Maybe<UploadType>;
};

export type ProfileInput = {
  avatar: InputMaybe<Scalars["String"]["input"]>;
};

export type ProfileType = {
  avatar?: Maybe<UploadType>;
  birthDate?: Maybe<Scalars["DateTime"]["output"]>;
  displayName?: Maybe<Scalars["String"]["output"]>;
  /** Direct reference to User.email. */
  email?: Maybe<Scalars["String"]["output"]>;
  firstName?: Maybe<Scalars["String"]["output"]>;
  guid: Scalars["UUID"]["output"];
  hasPin: Scalars["Boolean"]["output"];
  isActive: Scalars["Boolean"]["output"];
  lastName?: Maybe<Scalars["String"]["output"]>;
  nickname?: Maybe<Scalars["String"]["output"]>;
  preferredLanguage?: Maybe<Scalars["String"]["output"]>;
  signature?: Maybe<UploadType>;
  timezone?: Maybe<Scalars["String"]["output"]>;
  username?: Maybe<Scalars["String"]["output"]>;
};

export type ProjectSecretBundleKeyInput = {
  bundleId: Scalars["GUID"]["input"];
  key: Scalars["String"]["input"];
  value: InputMaybe<Scalars["String"]["input"]>;
};

export type PromoteDeploymentInput = {
  appSlug: Scalars["String"]["input"];
  sourceEnvironmentName: Scalars["String"]["input"];
  targetEnvironmentName: Scalars["String"]["input"];
};

export type ProposeSecretChangeInput = {
  appSlug: Scalars["String"]["input"];
  attachmentId: InputMaybe<Scalars["GUID"]["input"]>;
  bundleSlug: InputMaybe<Scalars["String"]["input"]>;
  environmentName: InputMaybe<Scalars["String"]["input"]>;
  key: InputMaybe<Scalars["String"]["input"]>;
  op: Scalars["String"]["input"];
  prefix: InputMaybe<Scalars["String"]["input"]>;
  value: InputMaybe<Scalars["String"]["input"]>;
};

export type Providerpluginconfigpayload = {
  organizationScoped: Scalars["Boolean"]["output"];
  pluginSlug: Scalars["String"]["output"];
};

export type ProviderpluginconfigpayloadMutationResult = {
  data?: Maybe<Providerpluginconfigpayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ProvisionClusterModelInput = {
  allowSubscriptions: Scalars["Boolean"]["input"];
  clusterId: Scalars["GUID"]["input"];
  computeMode: Scalars["String"]["input"];
  cpuKvCacheGiB: InputMaybe<Scalars["Int"]["input"]>;
  cpuRequest: Scalars["String"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  gpuCount: Scalars["Int"]["input"];
  memoryRequest: Scalars["String"]["input"];
  modelRepo: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
  organizationId: Scalars["GUID"]["input"];
  revisionSha: Scalars["String"]["input"];
};

export type ProvisionManagedDomainPayload = {
  message: Scalars["String"]["output"];
  nameservers: Array<Scalars["String"]["output"]>;
  workflowId: Scalars["String"]["output"];
  zone: Scalars["String"]["output"];
};

export type ProvisionManagedDomainPayloadMutationResult = {
  data?: Maybe<ProvisionManagedDomainPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type ProvisionManagedServiceInput = {
  appSlug: Scalars["String"]["input"];
  config: InputMaybe<Scalars["JSON"]["input"]>;
  environmentName: Scalars["String"]["input"];
  isolation: InputMaybe<Scalars["String"]["input"]>;
  kind: Scalars["String"]["input"];
  name: InputMaybe<Scalars["String"]["input"]>;
  variant: InputMaybe<Scalars["String"]["input"]>;
};

export type ProvisionProjectManagedServiceInput = {
  agentEnvironmentSpecSlugs: Array<Scalars["String"]["input"]>;
  appEnvironmentIds: Array<Scalars["GUID"]["input"]>;
  clusterId: Scalars["GUID"]["input"];
  config: InputMaybe<Scalars["JSON"]["input"]>;
  environmentName: Scalars["String"]["input"];
  isolation: InputMaybe<Scalars["String"]["input"]>;
  kind: Scalars["String"]["input"];
  name: InputMaybe<Scalars["String"]["input"]>;
  projectId: Scalars["GUID"]["input"];
  variant: InputMaybe<Scalars["String"]["input"]>;
};

export type PushCiSecretsToRepoInput = {
  appSlug: Scalars["String"]["input"];
};

export type PushCiWorkflowInput = {
  appId: Scalars["GUID"]["input"];
  branch: InputMaybe<Scalars["String"]["input"]>;
  commitMessage: InputMaybe<Scalars["String"]["input"]>;
  connectionId: Scalars["GUID"]["input"];
  filePath: InputMaybe<Scalars["String"]["input"]>;
};

export type PushCiWorkflowToRepoInput = {
  appSlug: Scalars["String"]["input"];
};

export type PushManifestToRepoInput = {
  branchName: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  prBody: InputMaybe<Scalars["String"]["input"]>;
  prTitle: InputMaybe<Scalars["String"]["input"]>;
};

export type Query = {
  agent?: Maybe<AstroliftAgentDetail>;
  agentBox?: Maybe<AstroliftAgentBox>;
  agentBoxPods: Array<AstroliftAppPod>;
  agentBoxes: Array<AstroliftAgentBox>;
  agentEnvironmentSpec?: Maybe<AstroliftAgentEnvironmentSpec>;
  agentEnvironmentSpecSecretBundleAttachments: Array<AstroliftAgentSecretBundleAttachment>;
  agentEnvironmentSpecSecretStatus: Array<AstroliftAgentSecretStatus>;
  agentEnvironmentSpecSecretStatusPage: AstroliftAgentSecretStatusPage;
  agentEnvironmentSpecs: Array<AstroliftAgentEnvironmentSpec>;
  agentEnvironmentSpecsPage: AstroliftAgentEnvironmentSpecPage;
  agentFleet: Array<AstroliftAgentListItem>;
  agentFleetPage: AstroliftAgentListItemPage;
  agentGallery: Array<AstroliftAgentTask>;
  agentLiveStatus: Array<AstroliftAgentLiveStatus>;
  agentQuarantines: Array<AstroliftAgentQuarantine>;
  agentRuntimes: Array<AstroliftAgentRuntime>;
  agentSecretBundles: Array<AstroliftAgentSecretBundle>;
  agentTask?: Maybe<AstroliftAgentTask>;
  agentTaskBacklog?: Maybe<AstroliftAgentTaskBacklog>;
  agentTaskByClientRequestId?: Maybe<AstroliftAgentTask>;
  agentTaskCallbackPolicy: AgentTaskCallbackPolicy;
  agentTaskEvents: Array<AstroliftAgentTaskEvent>;
  agentTaskInputMessage?: Maybe<AstroliftAgentTaskInputMessage>;
  agentTaskInteractions: Array<AstroliftAgentInteraction>;
  agentTaskLogs: Array<Scalars["String"]["output"]>;
  agentTaskLogsPage: AstroliftAgentTaskLogPage;
  agentTaskTransitionsSince: Array<AstroliftAgentTask>;
  agentTasks: Array<AstroliftAgentTask>;
  agentTasksPage: AstroliftAgentTaskPage;
  /** @deprecated Unbounded: returns every trigger bound to the agent in one response. Use agentTriggersPage instead. */
  agentTriggers: Array<AstroliftAgentTrigger>;
  agentTriggersPage: AstroliftAgentTriggerPage;
  agentUpcomingRuns: AstroliftAgentUpcomingRunPage;
  agentWorkloads: Array<AstroliftAgentListItem>;
  assignableAstroliftProjects: Array<AstroliftProject>;
  astroliftAccessOn: AstroliftAccessEntryPage;
  astroliftActiveIdentityProvider?: Maybe<AstroliftIdentityProvider>;
  astroliftActiveSessions: Array<AstroliftActiveSession>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftAgentRunsPage. */
  astroliftAgentRuns: Array<AstroliftAgentRun>;
  astroliftAgentRunsPage: AstroliftAgentRunPage;
  astroliftAlertEvent?: Maybe<AstroliftAlertEvent>;
  astroliftAlertEventSummary: AstroliftAlertEventSummary;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftAlertEventsPage. */
  astroliftAlertEvents: Array<AstroliftAlertEvent>;
  astroliftAlertEventsPage: AstroliftAlertEventPage;
  astroliftAlertRule?: Maybe<AstroliftAlertRule>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftAlertRulesPage. */
  astroliftAlertRules: Array<AstroliftAlertRule>;
  astroliftAlertRulesPage: AstroliftAlertRulePage;
  astroliftApiTokenScopeCatalog: AstroliftApiTokenScopeCatalog;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftApiTokensPage. */
  astroliftApiTokens: Array<AstroliftApiToken>;
  astroliftApiTokensPage: AstroliftApiTokenPage;
  astroliftApp?: Maybe<AstroliftRegisteredApp>;
  astroliftAppAccess?: Maybe<AstroliftAppAccess>;
  astroliftAppAccessPreview?: Maybe<AstroliftAppAccessPreview>;
  astroliftAppCertificates: AstroliftAppCertificatesResult;
  astroliftAppCountForCluster: Scalars["Int"]["output"];
  /** Read-only APP_READ projection for an exact live app/environment. Expected cluster/provider GUIDs refuse reassignment. Persisted observations do not prove live health, TLS binding or renewal; mutation authority is unchanged. */
  astroliftAppDependencyContext?: Maybe<AppDependencyContext>;
  astroliftAppDeployTokenRotationMetadata?: Maybe<AstroliftDeployTokenRotationMetadata>;
  /** @deprecated Caps at 100 rows with no way to reach the 101st. Use astroliftAppDeployTokensPage. */
  astroliftAppDeployTokens: Array<AstroliftDeployToken>;
  astroliftAppDeployTokensPage: AstroliftDeployTokenPage;
  astroliftAppDnsRecords: AstroliftAppDnsRecordsResult;
  astroliftAppDoctor: AstroliftAppDoctorReport;
  astroliftAppDomains: Array<AstroliftAppDomain>;
  astroliftAppEndpointMetrics: Array<AstroliftAppEndpointMetric>;
  astroliftAppExecTarget?: Maybe<AstroliftAppExecTarget>;
  astroliftAppGoldenSignals: AstroliftAppGoldenSignalsResult;
  astroliftAppHealthSummary: Array<AstroliftAppHealthSummary>;
  astroliftAppIdentityBinding: AstroliftAppIdentityBindingResult;
  astroliftAppLogs: AstroliftAppLogPage;
  astroliftAppManagedServiceMetrics?: Maybe<AstroliftManagedServiceMetrics>;
  astroliftAppMetricNames: AstroliftAppMetricNames;
  astroliftAppMetrics?: Maybe<AstroliftAppMetrics>;
  astroliftAppPods: Array<AstroliftAppPod>;
  astroliftAppSecretBundleAttachments: Array<AstroliftAppSecretBundleAttachment>;
  astroliftAppSecretHistory: Array<AstroliftSecretHistoryEntry>;
  astroliftAppSecrets: Array<AstroliftAppSecret>;
  astroliftAppStatusCodeBreakdown?: Maybe<AstroliftStatusCodeBreakdown>;
  /** @deprecated Returns every grant in one unbounded response. Use astroliftAppTeamAccessesPage. */
  astroliftAppTeamAccesses: Array<AstroliftAppTeamAccess>;
  astroliftAppTeamAccessesPage: AstroliftAppTeamAccessPage;
  astroliftAppTraces: Array<AstroliftAppTrace>;
  astroliftAppUptime?: Maybe<AstroliftAppUptime>;
  astroliftAppUrlHealth?: Maybe<AstroliftAppUrlHealth>;
  astroliftAppUrlProbeHistory: Array<AstroliftAppUrlHealth>;
  astroliftApps: Array<AstroliftRegisteredApp>;
  astroliftAppsPage: AstroliftRegisteredAppPage;
  astroliftAuditEvents: Array<AstroliftAuditEvent>;
  astroliftAuditEventsPage: AstroliftAuditEventPage;
  astroliftAuditRetention: AstroliftAuditRetention;
  astroliftAvailableRepos: AstroliftRemoteRepoList;
  astroliftBudgets: Array<AstroliftBudget>;
  astroliftCluster?: Maybe<AstroliftTenantCluster>;
  astroliftClusterAuthUsers?: Maybe<AstroliftClusterAuthUsers>;
  astroliftClusterBootstrapPlan?: Maybe<AstroliftClusterBootstrapPlan>;
  astroliftClusterCertificates: AstroliftClusterCertificates;
  astroliftClusterCount: Scalars["Int"]["output"];
  astroliftClusterHealth?: Maybe<AstroliftClusterHealth>;
  astroliftClusterLifecycleAudit: Array<AstroliftClusterLifecycleAuditEntry>;
  astroliftClusterLiveState?: Maybe<AstroliftClusterLiveState>;
  astroliftClusterModelDensity?: Maybe<ClusterModelDensity>;
  astroliftClusterPrometheusMetrics: AstroliftClusterPrometheusMetrics;
  astroliftClusterPrometheusRangeMetrics: AstroliftClusterPrometheusRangeMetrics;
  astroliftClusterSystemMetrics: AstroliftClusterSystemMetrics;
  astroliftClusterWorkloadHealth: Array<AstroliftClusterWorkloadHealth>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftClustersPage. */
  astroliftClusters: Array<AstroliftTenantCluster>;
  astroliftClustersPage: AstroliftTenantClusterPage;
  astroliftCognitoUserPoolClients: Array<AstroliftCognitoUserPoolClient>;
  astroliftCognitoUserPools: Array<AstroliftCognitoUserPool>;
  astroliftCommandRun?: Maybe<AstroliftCommandRun>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftCommandRunsPage. */
  astroliftCommandRuns: Array<AstroliftCommandRun>;
  astroliftCommandRunsPage: AstroliftCommandRunPage;
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
  astroliftDeploymentRunLogDownload?: Maybe<AstroliftDeploymentRunLogDownload>;
  astroliftDeploymentRunLogPage: AstroliftDeploymentRunLogPage;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftDeploymentsPage. */
  astroliftDeployments: Array<AstroliftDeployment>;
  astroliftDeploymentsPage: AstroliftDeploymentPage;
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
  astroliftEnvironmentsPage: AstroliftAppEnvironmentPage;
  astroliftEvent?: Maybe<AstroliftEvent>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftEventsPage. */
  astroliftEvents: Array<AstroliftEvent>;
  /** @deprecated Caps at 500 buckets folded from a bounded 10k-row scan, so activity older than the scan window is unreachable. Use astroliftEventsAggregatedPage. */
  astroliftEventsAggregated: Array<AstroliftAggregatedEvent>;
  astroliftEventsAggregatedPage: AstroliftAggregatedEventPage;
  astroliftEventsPage: AstroliftEventPage;
  astroliftExecutePromql: AstroliftExecutePromqlResult;
  astroliftGrantPreview: AstroliftGrantPreview;
  astroliftGroupRoleMappingsPage: AstroliftGroupRoleMappingPage;
  astroliftHuggingFaceModel: HuggingFaceModelResult;
  astroliftHuggingFaceModels: HuggingFaceModelsPage;
  astroliftIdentityProviders: Array<AstroliftIdentityProvider>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftInvitationsPage. */
  astroliftInvitations: Array<AstroliftInvitation>;
  astroliftInvitationsCsv: AstroliftIdentityCsvExport;
  astroliftInvitationsPage: AstroliftInvitationPage;
  astroliftManagedDomains: Array<AstroliftManagedDomain>;
  astroliftManagedService?: Maybe<AstroliftManagedServiceContext>;
  astroliftManagedServiceAttachmentsPage?: Maybe<AstroliftManagedServiceAttachmentContextPage>;
  astroliftManagedServiceCostPreview?: Maybe<AstroliftManagedServiceCostPreview>;
  astroliftManagedServiceObjects?: Maybe<AstroliftManagedServiceObjects>;
  astroliftManagedServiceQueueDepth?: Maybe<AstroliftManagedServiceQueueDepth>;
  /** @deprecated Unbounded, and applies no ordering at all — row order is whatever Postgres returns. Use astroliftManagedServicesPage. */
  astroliftManagedServices: Array<AstroliftManagedService>;
  astroliftManagedServicesPage: AstroliftManagedServicePage;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftMembersPage. */
  astroliftMembers: Array<AstroliftMember>;
  astroliftMembersCsv: AstroliftIdentityCsvExport;
  astroliftMembersPage: AstroliftMemberPage;
  astroliftModelDeploymentMetrics?: Maybe<ModelDeploymentMetrics>;
  astroliftModelEndpoint?: Maybe<AstroliftManagedService>;
  astroliftModelEndpoints: Array<AstroliftManagedService>;
  astroliftModelEndpointsPage: AstroliftManagedServicePage;
  astroliftModelPromptReadiness?: Maybe<AstroliftModelPromptReadiness>;
  astroliftMyAlertSubscriptions: Array<AstroliftUserAlertSubscription>;
  astroliftMyApps: Array<AstroliftRegisteredApp>;
  astroliftMyAppsPage: AstroliftRegisteredAppPage;
  astroliftMyConnectedAccounts: Array<AstroliftMyConnectedAccount>;
  astroliftMyDevices: Array<AstroliftDeviceRegistration>;
  astroliftMyMobileDevices: Array<AstroliftDeviceRegistration>;
  astroliftMyNotificationPreferences: Array<AstroliftNotificationPreference>;
  astroliftMyNotifications: Array<AstroliftNotification>;
  astroliftMyPermissions: Array<Scalars["String"]["output"]>;
  astroliftMyProfile?: Maybe<AstroliftMyProfile>;
  astroliftMyUiPreferences?: Maybe<AstroliftUiPreferences>;
  astroliftNavTree?: Maybe<AstroliftNavTree>;
  astroliftObservabilityRetention: Array<ObservabilityRetentionType>;
  astroliftOrgMembersForApprovalPicker: Array<AstroliftApproverUser>;
  astroliftOrganization?: Maybe<AstroliftOrganization>;
  astroliftOrganizationAllowlistDomains: Array<AstroliftOrganizationAllowlistedDomain>;
  astroliftOrganizations: Array<AstroliftOrganization>;
  astroliftPipeline?: Maybe<AstroliftPipeline>;
  astroliftPipelineRun?: Maybe<AstroliftPipelineRun>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftPipelineRunsPage. */
  astroliftPipelineRuns: Array<AstroliftPipelineRun>;
  astroliftPipelineRunsPage: AstroliftPipelineRunPage;
  astroliftPipelineSecrets: Array<AstroliftPipelineSecret>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftPipelinesPage. */
  astroliftPipelines: Array<AstroliftPipeline>;
  astroliftPipelinesPage: AstroliftPipelinePage;
  astroliftPlatformApiUrl: Scalars["String"]["output"];
  astroliftPodResourceUsage?: Maybe<AstroliftPodResourceUsage>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftPoliciesPage. */
  astroliftPolicies: Array<AstroliftPolicy>;
  astroliftPoliciesPage: AstroliftPolicyPage;
  astroliftPolicy?: Maybe<AstroliftPolicy>;
  astroliftPolicyConditionCatalog: AstroliftPolicyConditionCatalog;
  astroliftPolicySimulation: AstroliftPolicySimulation;
  astroliftPreviewDeploymentsPage: AstroliftPreviewDeploymentPage;
  astroliftPreviewEnvironment?: Maybe<AstroliftPreviewEnvironment>;
  astroliftPreviewEnvironmentCounts: AstroliftPreviewEnvironmentCounts;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftPreviewEnvironmentsPage for browsing and astroliftPreviewEnvironment for exact detail. */
  astroliftPreviewEnvironments: Array<AstroliftPreviewEnvironment>;
  astroliftPreviewEnvironmentsPage: AstroliftPreviewEnvironmentPage;
  astroliftPrincipalSearch: AstroliftPrincipalPage;
  astroliftProjectManagedService?: Maybe<AstroliftManagedServiceContext>;
  astroliftProjectManagedServiceAttachmentsPage?: Maybe<AstroliftManagedServiceAttachmentContextPage>;
  astroliftProjectManagedServiceCatalog: Array<AstroliftManagedServiceCatalogEntry>;
  astroliftProjectManagedServices: Array<AstroliftManagedService>;
  astroliftProjectManagedServicesPage: AstroliftManagedServiceContextPage;
  astroliftProjectResourceClusters: Array<AstroliftTenantCluster>;
  astroliftProjectSecretBundles: Array<AstroliftSecretBundle>;
  astroliftProjectSlugAvailable: Scalars["Boolean"]["output"];
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftProjectsPage. */
  astroliftProjects: Array<AstroliftProject>;
  astroliftProjectsPage: AstroliftProjectPage;
  /** An authenticated public provider catalog reference without list caps. expectedId refuses a replaced slug; no tenant configuration or credentials are returned. */
  astroliftProviderPlugin?: Maybe<AstroliftProviderPlugin>;
  astroliftProviderPlugins: Array<AstroliftProviderPlugin>;
  astroliftProviderRegions: Array<AstroliftProviderRegion>;
  astroliftQuotaUsageHistory: Array<AstroliftQuotaUsagePoint>;
  astroliftQuotas: Array<AstroliftQuota>;
  astroliftRecentActivity: AstroliftActivityPage;
  astroliftRecentClusterWorkflows: Array<AstroliftClusterWorkflowRun>;
  astroliftRenderedManifest?: Maybe<AstroliftRenderedManifest>;
  astroliftRole?: Maybe<AstroliftRole>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftRoleBindingsPage. */
  astroliftRoleBindings: Array<AstroliftRoleBinding>;
  astroliftRoleBindingsCsv: AstroliftIdentityCsvExport;
  astroliftRoleBindingsPage: AstroliftRoleBindingPage;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftRolesPage. */
  astroliftRoles: Array<AstroliftRole>;
  astroliftRolesICanGrant: Array<AstroliftRole>;
  astroliftRolesPage: AstroliftRolePage;
  /** Everything that ran, of every kind, in one cursor list (#2152): agent tasks, workflow runs, deployments, job runs and task runs, newest first. Each kind is read under its own permission and narrowed to the caller's scopes. */
  astroliftRunAudit: AstroliftRunAuditItemPage;
  astroliftScheduledJobRun?: Maybe<AstroliftScheduledJobRun>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftScheduledJobRunsPage. */
  astroliftScheduledJobRuns: Array<AstroliftScheduledJobRun>;
  astroliftScheduledJobRunsPage: AstroliftScheduledJobRunPage;
  astroliftSearchableUsers: Array<AstroliftSearchableUser>;
  astroliftSecretBundles: Array<AstroliftSecretBundle>;
  astroliftSecretChangeProposal?: Maybe<AstroliftSecretChangeProposal>;
  astroliftSecretChangeProposalMetadata?: Maybe<AstroliftSecretChangeProposalMetadata>;
  astroliftSecretChangeProposals: Array<AstroliftSecretChangeProposal>;
  astroliftSecretChangeProposalsPage: AstroliftSecretChangeProposalPage;
  /** Multi-install handshake. Returns version, capabilities, feature flags, install identity, and server time so a mobile / CLI / SDK client can decide which UI to render before logging in. */
  astroliftServerInfo: AstroliftServerInfo;
  astroliftSharedModelPromptReadiness?: Maybe<AstroliftModelPromptReadiness>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftSourceConnectionsPage. */
  astroliftSourceConnections: Array<AstroliftSourceConnection>;
  astroliftSourceConnectionsPage: AstroliftSourceConnectionPage;
  astroliftSourceFile: AstroliftSourceFile;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftSshDeployKeysPage. */
  astroliftSshDeployKeys: Array<AstroliftSshDeployKey>;
  astroliftSshDeployKeysPage: AstroliftSshDeployKeyPage;
  astroliftTaskRun?: Maybe<AstroliftTaskRun>;
  /** @deprecated Caps at 500 rows with no way to reach the 501st. Use astroliftTaskRunsPage. */
  astroliftTaskRuns: Array<AstroliftTaskRun>;
  astroliftTaskRunsPage: AstroliftTaskRunPage;
  astroliftTeamMembers: Array<AstroliftMember>;
  astroliftTeamSlugAvailable: Scalars["Boolean"]["output"];
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftTeamsPage. */
  astroliftTeams: Array<AstroliftTeam>;
  astroliftTeamsPage: AstroliftTeamPage;
  astroliftTopologyTraffic?: Maybe<TopologyTraffic>;
  astroliftTraceSpans: Array<AstroliftTraceSpan>;
  /** @deprecated Caps at 100 attempts with no way to reach the 101st. Use astroliftWebhookDeliveriesPage. */
  astroliftWebhookDeliveries: Array<AstroliftWebhookDelivery>;
  astroliftWebhookDeliveriesPage: AstroliftWebhookDeliveryPage;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftWebhookSubscriptionsPage. */
  astroliftWebhookSubscriptions: Array<AstroliftWebhookSubscription>;
  astroliftWebhookSubscriptionsPage: AstroliftWebhookSubscriptionPage;
  astroliftWorkflowInstance?: Maybe<AstroliftWorkflowInstance>;
  astroliftWorkflowInstanceDetail?: Maybe<AstroliftWorkflowInstanceDetail>;
  astroliftWorkflowInstances: AstroliftWorkflowInstancePage;
  astroliftWorkflowRuns: Array<AstroliftWorkflowRun>;
  astroliftWorkload?: Maybe<AstroliftWorkload>;
  astroliftWorkloadActionTarget?: Maybe<AstroliftWorkloadActionTarget>;
  astroliftWorkloadIdentityGrants: Array<AstroliftWorkloadIdentityGrant>;
  astroliftWorkloadManifest?: Maybe<AstroliftWorkloadManifest>;
  astroliftWorkloadPodStatusBreakdown: Array<AstroliftWorkloadPodStatusBucket>;
  astroliftWorkloadResourceUsage?: Maybe<AstroliftWorkloadResourceUsage>;
  astroliftWorkloadScalingStatus?: Maybe<AstroliftWorkloadScalingStatus>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use astroliftWorkloadsPage. */
  astroliftWorkloads: Array<AstroliftWorkload>;
  astroliftWorkloadsPage: AstroliftWorkloadPage;
  astroliftZentinelleConnection?: Maybe<AstroliftZentinelleConnection>;
  /**
   * Query mutation audit logs. Superuser only.
   * @deprecated Caps at 200 rows with no way to reach the 201st. Use auditLogsPage.
   */
  auditLogs: Array<AuditLogEntry>;
  /** Cursor-paginated mutation audit log. Superuser only. */
  auditLogsPage: AuditLogEntryPage;
  brief?: Maybe<AstroliftBrief>;
  clusterModelDeployment?: Maybe<ClusterModelDeployment>;
  clusterModelDeploymentsPage: ClusterModelDeploymentPage;
  clusterModelPlacementClustersPage: ModelPlacementClusterPage;
  clusterModelRuntimeAdmission: ModelRuntimeAdmission;
  clusterModelSubscriptionTargetsPage: ModelSubscriptionTargetPage;
  clusterModelSubscriptionsPage: ModelSubscriptionPage;
  dispatchers: Array<AstroliftDispatcherInstance>;
  /** List all effective permissions for a user, with the role bindings that grant each one. */
  effectivePermissions: Array<PermissionEntry>;
  exportWorkflowManifest: WorkflowManifestExportType;
  formDefinition?: Maybe<AstroliftFormDefinition>;
  formDefinitions: Array<AstroliftFormDefinition>;
  formFieldTypes: Array<Scalars["String"]["output"]>;
  formSubmissions: Array<AstroliftFormSubmission>;
  me?: Maybe<AstroliftMe>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use membersPage. */
  members: Array<OrganizationMemberType>;
  /** Cursor-paginated list of members in the caller's organizations. */
  membersPage: OrganizationMemberTypePage;
  orgSkillRepos: Array<AstroliftOrgSkillRepo>;
  orgToolDefs: Array<AstroliftToolDef>;
  orgToolDefsPage: AstroliftToolDefPage;
  organization?: Maybe<OrganizationType>;
  /** @deprecated Caps at 200 rows with no way to reach the 201st. Use organizationsPage. */
  organizations: Array<OrganizationType>;
  /** Cursor-paginated list of the caller's organizations. */
  organizationsPage: OrganizationTypePage;
  /** Open human_gate stage executions across the org's runs that the caller may decide, newest first (#1820). */
  pendingHumanGates: Array<PendingHumanGate>;
  /** Compare effective permissions between two users, anywhere in the active organization or on a target scope (scopeType, scopeId). Superuser or org.manage_members; both users must be members of the active organization. */
  permissionCompare?: Maybe<PermissionComparison>;
  /** Diagnose why a user can or can't perform a specific permission, optionally on a target scope (scopeType ORG, TEAM, PROJECT, APP or AGENT; scopeId its guid). Self, superuser or org.manage_members; the user must be a member of the active organization. */
  permissionDiagnose?: Maybe<PermissionDiagnosis>;
  pipelineJobRunsPage: AstroliftJobRunPage;
  pipelineStartRequest?: Maybe<AstroliftPipelineRun>;
  pipelineStepRunsPage: AstroliftStepRunPage;
  previewAstroliftDeregister?: Maybe<AstroliftDeregisterPreview>;
  previewAstroliftForceRedeploy?: Maybe<AstroliftForceRedeployPreview>;
  previewWorkflowManifest: WorkflowManifestPreviewType;
  scanAgentManifests: AstroliftScanAgentManifestsResult;
  scanAppManifests: AstroliftScanAppManifestsResult;
  scanCloudOrphans: CloudOrphanReportType;
  skill?: Maybe<AstroliftSkill>;
  skills: Array<AstroliftSkill>;
  skillsPage: AstroliftSkillPage;
  toolDef?: Maybe<AstroliftToolDef>;
  toolDefs: Array<AstroliftToolDef>;
  /** One configured Workflow by slug, with its recent runs. */
  workflow?: Maybe<ConfiguredWorkflow>;
  /** One visible workflow definition by slug (prefers the org's over a global). */
  workflowDefinition?: Maybe<WorkflowDefinitionSummary>;
  /** Review one exact definition GUID and its canonical input/revision contract; no slug substitution. */
  workflowDefinitionById?: Maybe<ReviewedWorkflowDefinition>;
  /** One run of a workflow definition by guid, or null (#2155). */
  workflowDefinitionRun?: Maybe<WorkflowDefinitionRun>;
  /** Recent runs of workflow definitions visible in the caller's organization. */
  workflowDefinitionRuns: Array<WorkflowDefinitionRun>;
  /** Runs of workflow definitions visible in the caller's organization, cursor-paged, with search and filters (#2155). */
  workflowDefinitionRunsPage: WorkflowDefinitionRunPage;
  /** Reconcile the authenticated actor's original reviewed start request without dispatching another run. */
  workflowDefinitionStartRequest?: Maybe<WorkflowDefinitionStart>;
  /**
   * Workflow definitions visible to the caller: their org's UNION all platform-global (spec 40 §2.1).
   * @deprecated Unbounded — returns every visible definition in one response. Use workflowDefinitionsPage.
   */
  workflowDefinitions: Array<WorkflowDefinitionSummary>;
  /** Cursor-paginated page of the workflow definitions visible to the caller, by name (A→Z). */
  workflowDefinitionsPage: WorkflowDefinitionSummaryPage;
  /** One owned execution by the WorkflowRun ID returned on dispatch or its GUID. */
  workflowExecution?: Maybe<WorkflowExecution>;
  /** Recorded stage history of an exact owned execution, newest first. */
  workflowExecutionStages?: Maybe<WorkflowExecutionStages>;
  /** Get a workflow instance by ID. */
  workflowInstance?: Maybe<WorkflowInstanceType>;
  /** List workflow instances for a specific object. */
  workflowInstances: Array<WorkflowInstanceType>;
  /** Runs (tier 3) of one configured Workflow, newest first. */
  workflowRuns: Array<WorkflowRun>;
  /** List stage executions for a WorkflowRun (by workflow_id + run_id). */
  workflowStageExecutions: Array<WorkflowStageExecutionType>;
  /** List stages for a workflow definition by slug. */
  workflowStages: Array<WorkflowStageType>;
  /**
   * List the org's configured Workflows (tier 2).
   * @deprecated Unbounded — returns every Workflow the org owns in one response. Use workflowsPage.
   */
  workflows: Array<ConfiguredWorkflow>;
  /** Cursor-paginated page of the org's configured Workflows (tier 2). */
  workflowsPage: ConfiguredWorkflowPage;
};

export type QueryAgentArgs = {
  orgId: Scalars["ID"]["input"];
  slug: Scalars["String"]["input"];
};

export type QueryAgentBoxArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryAgentBoxPodsArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryAgentBoxesArgs = {
  includeEnded?: Scalars["Boolean"]["input"];
  orgId: Scalars["ID"]["input"];
};

export type QueryAgentEnvironmentSpecArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type QueryAgentEnvironmentSpecSecretBundleAttachmentsArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryAgentEnvironmentSpecSecretStatusArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryAgentEnvironmentSpecSecretStatusPageArgs = {
  filter?: InputMaybe<AstroliftAgentSecretStatusFilter>;
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  slug: Scalars["String"]["input"];
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAgentEnvironmentSpecsArgs = {
  orgId: Scalars["ID"]["input"];
};

export type QueryAgentEnvironmentSpecsPageArgs = {
  filter?: InputMaybe<AstroliftAgentEnvironmentSpecsFilter>;
  orgId: Scalars["ID"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAgentFleetArgs = {
  orgId: Scalars["ID"]["input"];
};

export type QueryAgentFleetPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftAgentFleetFilter>;
  limit?: Scalars["Int"]["input"];
  orgId: Scalars["ID"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAgentGalleryArgs = {
  orgId: Scalars["ID"]["input"];
};

export type QueryAgentLiveStatusArgs = {
  orgId: Scalars["ID"]["input"];
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  workloadId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryAgentSecretBundlesArgs = {
  envSpecSlug: Scalars["String"]["input"];
};

export type QueryAgentTaskArgs = {
  id: Scalars["ID"]["input"];
};

export type QueryAgentTaskBacklogArgs = {
  orgId: Scalars["ID"]["input"];
  taskId: Scalars["ID"]["input"];
};

export type QueryAgentTaskByClientRequestIdArgs = {
  clientRequestId: Scalars["String"]["input"];
  orgId: Scalars["ID"]["input"];
};

export type QueryAgentTaskEventsArgs = {
  after?: Scalars["Int"]["input"];
  limit?: Scalars["Int"]["input"];
  orgId: Scalars["ID"]["input"];
  taskId: Scalars["ID"]["input"];
};

export type QueryAgentTaskInputMessageArgs = {
  clientRequestId: Scalars["String"]["input"];
  taskId: Scalars["ID"]["input"];
};

export type QueryAgentTaskInteractionsArgs = {
  limit?: Scalars["Int"]["input"];
  orgId: Scalars["ID"]["input"];
  since?: InputMaybe<Scalars["DateTime"]["input"]>;
  taskId: Scalars["ID"]["input"];
};

export type QueryAgentTaskLogsArgs = {
  id: Scalars["ID"]["input"];
  tail?: Scalars["Int"]["input"];
};

export type QueryAgentTaskLogsPageArgs = {
  cursor?: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["ID"]["input"];
  limit?: Scalars["Int"]["input"];
};

export type QueryAgentTaskTransitionsSinceArgs = {
  limit?: Scalars["Int"]["input"];
  orgId: Scalars["ID"]["input"];
  since?: InputMaybe<Scalars["DateTime"]["input"]>;
};

export type QueryAgentTasksArgs = {
  orgId: Scalars["ID"]["input"];
  status?: InputMaybe<Scalars["String"]["input"]>;
  workloadId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryAgentTasksPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftAgentTasksFilter>;
  limit?: Scalars["Int"]["input"];
  orgId: Scalars["ID"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
  workloadId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryAgentTriggersArgs = {
  agentSlug: Scalars["String"]["input"];
  orgId: Scalars["ID"]["input"];
};

export type QueryAgentTriggersPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  agentSlug: Scalars["String"]["input"];
  limit?: Scalars["Int"]["input"];
  orgId: Scalars["ID"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAgentUpcomingRunsArgs = {
  agent?: InputMaybe<Array<Scalars["String"]["input"]>>;
  orgId: Scalars["ID"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  perAgent?: Scalars["Int"]["input"];
  project?: InputMaybe<Array<Scalars["String"]["input"]>>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  withinHours?: InputMaybe<Scalars["Int"]["input"]>;
};

export type QueryAgentWorkloadsArgs = {
  dispatchable?: Scalars["Boolean"]["input"];
  orgId: Scalars["ID"]["input"];
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAccessOnArgs = {
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  scopeId: Scalars["String"]["input"];
  scopeKind: Scalars["String"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAgentRunsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAgentRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAlertEventArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftAlertEventSummaryArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAlertEventsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  ruleId?: InputMaybe<Scalars["GUID"]["input"]>;
  unresolvedOnly?: Scalars["Boolean"]["input"];
};

export type QueryAstroliftAlertEventsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  ruleId?: InputMaybe<Scalars["GUID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  unresolvedOnly?: Scalars["Boolean"]["input"];
};

export type QueryAstroliftAlertRuleArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftAlertRulesArgs = {
  activeOnly?: Scalars["Boolean"]["input"];
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  target?: InputMaybe<Scalars["String"]["input"]>;
  targetId?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAlertRulesPageArgs = {
  activeOnly?: Scalars["Boolean"]["input"];
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  target?: InputMaybe<Scalars["String"]["input"]>;
  targetId?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftApiTokensPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: InputMaybe<AstroliftListSortKey>;
};

export type QueryAstroliftAppArgs = {
  includeDrift?: Scalars["Boolean"]["input"];
  slug: Scalars["String"]["input"];
};

export type QueryAstroliftAppAccessArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppAccessPreviewArgs = {
  appSlug: Scalars["String"]["input"];
  groups: Array<Scalars["String"]["input"]>;
  users: Array<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppCertificatesArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppCountForClusterArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftAppDependencyContextArgs = {
  appId: Scalars["GUID"]["input"];
  environmentId: Scalars["GUID"]["input"];
  expectedClusterId?: InputMaybe<Scalars["GUID"]["input"]>;
  expectedProviderId?: InputMaybe<Scalars["GUID"]["input"]>;
};

export type QueryAstroliftAppDeployTokenRotationMetadataArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppDeployTokensArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppDeployTokensPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug: Scalars["String"]["input"];
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: InputMaybe<AstroliftListSortKey>;
};

export type QueryAstroliftAppDnsRecordsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppDoctorArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppDomainsArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppEndpointMetricsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  rangeSeconds?: InputMaybe<Scalars["Int"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppExecTargetArgs = {
  appSlug: Scalars["String"]["input"];
  container: Scalars["String"]["input"];
  environmentId: Scalars["GUID"]["input"];
  podName: Scalars["String"]["input"];
  workloadSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppGoldenSignalsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  rangeSeconds?: InputMaybe<Scalars["Int"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppIdentityBindingArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppLogsArgs = {
  appSlug: Scalars["String"]["input"];
  cursor?: InputMaybe<Scalars["String"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  expectedEnvironmentId?: InputMaybe<Scalars["GUID"]["input"]>;
  ifMatchEnvironmentVersion?: InputMaybe<Scalars["Int"]["input"]>;
  ifMatchPreviewVersion?: InputMaybe<Scalars["Int"]["input"]>;
  level?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  previewId?: InputMaybe<Scalars["GUID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  since: Scalars["DateTime"]["input"];
  until: Scalars["DateTime"]["input"];
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppManagedServiceMetricsArgs = {
  managedServiceId: Scalars["ID"]["input"];
  rangeSeconds?: InputMaybe<Scalars["Int"]["input"]>;
};

export type QueryAstroliftAppMetricNamesArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  lookbackSeconds?: Scalars["Int"]["input"];
};

export type QueryAstroliftAppMetricsArgs = {
  appSlug: Scalars["String"]["input"];
  timeRange?: Scalars["String"]["input"];
};

export type QueryAstroliftAppPodsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppSecretBundleAttachmentsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppSecretHistoryArgs = {
  appSlug: Scalars["String"]["input"];
  key: Scalars["String"]["input"];
};

export type QueryAstroliftAppSecretsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppStatusCodeBreakdownArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  rangeSeconds?: InputMaybe<Scalars["Int"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppTeamAccessesArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryAstroliftAppTeamAccessesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug: Scalars["String"]["input"];
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppTracesArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  limit?: InputMaybe<Scalars["Int"]["input"]>;
  minDurationMs?: InputMaybe<Scalars["Float"]["input"]>;
  operation?: InputMaybe<Scalars["String"]["input"]>;
  service?: InputMaybe<Scalars["String"]["input"]>;
  since: Scalars["String"]["input"];
  status?: InputMaybe<Scalars["String"]["input"]>;
  until: Scalars["String"]["input"];
};

export type QueryAstroliftAppUptimeArgs = {
  appSlug: Scalars["String"]["input"];
  windowHours?: Scalars["Int"]["input"];
};

export type QueryAstroliftAppUrlHealthArgs = {
  appSlug: Scalars["String"]["input"];
  forceRefresh?: Scalars["Boolean"]["input"];
  url: Scalars["String"]["input"];
};

export type QueryAstroliftAppUrlProbeHistoryArgs = {
  appSlug: Scalars["String"]["input"];
  limit?: Scalars["Int"]["input"];
  url: Scalars["String"]["input"];
};

export type QueryAstroliftAppsArgs = {
  includeFreshness?: Scalars["Boolean"]["input"];
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAppsPageArgs = {
  cursor?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftAppsListFilter>;
  includeArchived?: Scalars["Boolean"]["input"];
  includeFreshness?: Scalars["Boolean"]["input"];
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: AppsListSortKey;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAuditEventsArgs = {
  action?: InputMaybe<Scalars["String"]["input"]>;
  actorId?: InputMaybe<Scalars["String"]["input"]>;
  createdAtGte?: InputMaybe<Scalars["DateTime"]["input"]>;
  createdAtLte?: InputMaybe<Scalars["DateTime"]["input"]>;
  decision?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftAuditEventsPageArgs = {
  action?: InputMaybe<Scalars["String"]["input"]>;
  actorId?: InputMaybe<Scalars["String"]["input"]>;
  after?: InputMaybe<Scalars["String"]["input"]>;
  createdAtGte?: InputMaybe<Scalars["DateTime"]["input"]>;
  createdAtLte?: InputMaybe<Scalars["DateTime"]["input"]>;
  decision?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftAuditEventsFilter>;
  includeTotal?: Scalars["Boolean"]["input"];
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  subjectUserId?: InputMaybe<Scalars["String"]["input"]>;
  targetId?: InputMaybe<Scalars["String"]["input"]>;
  targetKind?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftAvailableReposArgs = {
  connectionId: Scalars["String"]["input"];
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftClusterArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryAstroliftClusterAuthUsersArgs = {
  clusterId: Scalars["GUID"]["input"];
  search?: Scalars["String"]["input"];
};

export type QueryAstroliftClusterBootstrapPlanArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftClusterCertificatesArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftClusterHealthArgs = {
  clusterId: Scalars["GUID"]["input"];
  eventLimit?: Scalars["Int"]["input"];
};

export type QueryAstroliftClusterLifecycleAuditArgs = {
  clusterId: Scalars["GUID"]["input"];
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftClusterLiveStateArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftClusterModelDensityArgs = {
  clusterId: Scalars["GUID"]["input"];
  end: Scalars["DateTime"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  start: Scalars["DateTime"]["input"];
};

export type QueryAstroliftClusterPrometheusMetricsArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftClusterPrometheusRangeMetricsArgs = {
  clusterId: Scalars["GUID"]["input"];
  rangeSeconds?: Scalars["Int"]["input"];
  stepSeconds?: Scalars["Int"]["input"];
};

export type QueryAstroliftClusterSystemMetricsArgs = {
  appNamespace?: InputMaybe<Scalars["String"]["input"]>;
  clusterId: Scalars["GUID"]["input"];
  rangeSeconds?: Scalars["Int"]["input"];
  stepSeconds?: Scalars["Int"]["input"];
};

export type QueryAstroliftClusterWorkloadHealthArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftClustersPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftClustersListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftCognitoUserPoolClientsArgs = {
  clusterId: Scalars["GUID"]["input"];
  poolId: Scalars["String"]["input"];
};

export type QueryAstroliftCognitoUserPoolsArgs = {
  clusterId: Scalars["GUID"]["input"];
};

export type QueryAstroliftCommandRunArgs = {
  id: Scalars["String"]["input"];
};

export type QueryAstroliftCommandRunsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftCommandRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftCommandRunsFilter>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftCompareDeploymentsArgs = {
  idA: Scalars["String"]["input"];
  idB: Scalars["String"]["input"];
};

export type QueryAstroliftContainersArgs = {
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftCostByBindingArgs = {
  days?: InputMaybe<Scalars["Int"]["input"]>;
  registeredAppSlug?: InputMaybe<Scalars["String"]["input"]>;
  window?: InputMaybe<CostWindow>;
};

export type QueryAstroliftCostSnapshotsArgs = {
  days?: InputMaybe<Scalars["Int"]["input"]>;
  limit?: Scalars["Int"]["input"];
  window?: InputMaybe<CostWindow>;
};

export type QueryAstroliftCostTrendArgs = {
  days?: InputMaybe<Scalars["Int"]["input"]>;
  window?: InputMaybe<CostWindow>;
};

export type QueryAstroliftDeploymentArgs = {
  id: Scalars["String"]["input"];
};

export type QueryAstroliftDeploymentApprovalHistoryArgs = {
  deploymentId: Scalars["String"]["input"];
};

export type QueryAstroliftDeploymentLogArgs = {
  deploymentId: Scalars["String"]["input"];
};

export type QueryAstroliftDeploymentMetricsArgs = {
  windowDays?: Scalars["Int"]["input"];
};

export type QueryAstroliftDeploymentReleaseNotesArgs = {
  deploymentId: Scalars["String"]["input"];
};

export type QueryAstroliftDeploymentRunLogDownloadArgs = {
  deploymentId: Scalars["String"]["input"];
};

export type QueryAstroliftDeploymentRunLogPageArgs = {
  cursor?: InputMaybe<Scalars["String"]["input"]>;
  deploymentId: Scalars["String"]["input"];
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftDeploymentsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftDeploymentsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftDeploymentsFilter>;
  isPreview?: InputMaybe<Scalars["Boolean"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
  statuses?: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type QueryAstroliftDnsCertificatesArgs = {
  dnsDriver: Scalars["String"]["input"];
};

export type QueryAstroliftDnsZonesArgs = {
  dnsDriver: Scalars["String"]["input"];
};

export type QueryAstroliftEmailEngagementMetricsArgs = {
  days?: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftEmailMessagesArgs = {
  eventKind?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  recipient?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftEmailServiceDetailArgs = {
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftEmailTemplateArgs = {
  managedServiceId: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
};

export type QueryAstroliftEmailTemplateStatsArgs = {
  days?: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
};

export type QueryAstroliftEmailTemplatesArgs = {
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftEnvironmentsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftEnvironmentsPageArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftEnvironmentsFilter>;
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftEventArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftEventsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  eventType?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  severity?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftEventsAggregatedArgs = {
  aggregateWindowSeconds?: Scalars["Int"]["input"];
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  eventType?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  severity?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftEventsAggregatedPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  aggregateWindowSeconds?: Scalars["Int"]["input"];
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  eventType?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  severity?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftEventsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  eventType?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  severity?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftExecutePromqlArgs = {
  appSlug: Scalars["String"]["input"];
  endUnix: Scalars["Int"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  query: Scalars["String"]["input"];
  startUnix: Scalars["Int"]["input"];
  stepSeconds: Scalars["Int"]["input"];
};

export type QueryAstroliftGrantPreviewArgs = {
  input: AstroliftGrantPreviewInput;
  limit?: InputMaybe<Scalars["Int"]["input"]>;
};

export type QueryAstroliftGroupRoleMappingsPageArgs = {
  groupExternalId?: InputMaybe<Scalars["String"]["input"]>;
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftHuggingFaceModelArgs = {
  repoId: Scalars["String"]["input"];
  revision?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftHuggingFaceModelsArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  author?: Scalars["String"]["input"];
  first?: Scalars["Int"]["input"];
  gated?: InputMaybe<Scalars["Boolean"]["input"]>;
  library?: Scalars["String"]["input"];
  license?: Scalars["String"]["input"];
  pipelineTag?: Scalars["String"]["input"];
  search?: Scalars["String"]["input"];
  sortBy?: Scalars["String"]["input"];
};

export type QueryAstroliftInvitationsArgs = {
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftInvitationsCsvArgs = {
  filter?: InputMaybe<AstroliftInvitationsListFilter>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftInvitationsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftInvitationsListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftManagedServiceArgs = {
  expectedContextRevision?: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftManagedServiceAttachmentsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  expectedContextRevision?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftManagedServiceCostPreviewArgs = {
  expectedContextRevision?: InputMaybe<Scalars["String"]["input"]>;
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftManagedServiceObjectsArgs = {
  limit?: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftManagedServiceQueueDepthArgs = {
  managedServiceId: Scalars["GUID"]["input"];
};

export type QueryAstroliftManagedServicesArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftManagedServicesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMembersArgs = {
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMembersCsvArgs = {
  filter?: InputMaybe<AstroliftMembersListFilter>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMembersPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftMembersListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftModelDeploymentMetricsArgs = {
  end: Scalars["DateTime"]["input"];
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  serviceId: Scalars["GUID"]["input"];
  start: Scalars["DateTime"]["input"];
};

export type QueryAstroliftModelEndpointArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftModelEndpointsPageArgs = {
  filter?: InputMaybe<AstroliftModelEndpointsFilter>;
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftModelPromptReadinessArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftMyAlertSubscriptionsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMyAppsArgs = {
  includeFreshness?: Scalars["Boolean"]["input"];
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMyAppsPageArgs = {
  cursor?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftAppsListFilter>;
  includeArchived?: Scalars["Boolean"]["input"];
  includeFreshness?: Scalars["Boolean"]["input"];
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  projectSlug?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: AppsListSortKey;
  sourceKind?: InputMaybe<AstroliftAppSourceKindFilter>;
  status?: InputMaybe<AstroliftAppListStatusFilter>;
  teamSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMyNotificationPreferencesArgs = {
  channel?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftMyNotificationsArgs = {
  limit?: Scalars["Int"]["input"];
  unreadOnly?: Scalars["Boolean"]["input"];
};

export type QueryAstroliftOrgMembersForApprovalPickerArgs = {
  orgSlug: Scalars["String"]["input"];
};

export type QueryAstroliftOrganizationArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryAstroliftPipelineArgs = {
  id: Scalars["String"]["input"];
};

export type QueryAstroliftPipelineRunArgs = {
  id: Scalars["String"]["input"];
};

export type QueryAstroliftPipelineRunsArgs = {
  limit?: Scalars["Int"]["input"];
  pipelineId: Scalars["String"]["input"];
};

export type QueryAstroliftPipelineRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  pipelineId: Scalars["String"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftPipelineSecretsArgs = {
  pipelineId: Scalars["GUID"]["input"];
};

export type QueryAstroliftPipelinesArgs = {
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftPipelinesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftPodResourceUsageArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  podName: Scalars["String"]["input"];
  rangeSeconds?: InputMaybe<Scalars["Int"]["input"]>;
};

export type QueryAstroliftPoliciesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftPoliciesListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: InputMaybe<AstroliftListSortKey>;
};

export type QueryAstroliftPolicyArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftPolicySimulationArgs = {
  days?: InputMaybe<Scalars["Int"]["input"]>;
  draft: AstroliftPolicyDraftInput;
  limit?: InputMaybe<Scalars["Int"]["input"]>;
};

export type QueryAstroliftPreviewDeploymentsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  expectedEnvironmentId: Scalars["GUID"]["input"];
  id: Scalars["GUID"]["input"];
  ifMatchEnvironmentVersion: Scalars["Int"]["input"];
  ifMatchPreviewVersion: Scalars["Int"]["input"];
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftPreviewEnvironmentArgs = {
  id: Scalars["GUID"]["input"];
  includeRuntimeCost?: Scalars["Boolean"]["input"];
};

export type QueryAstroliftPreviewEnvironmentCountsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftPreviewEnvironmentsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftPreviewEnvironmentsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftPreviewEnvironmentsFilter>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  statuses?: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type QueryAstroliftPrincipalSearchArgs = {
  filter?: InputMaybe<AstroliftPrincipalSearchFilter>;
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftProjectManagedServiceArgs = {
  expectedContextRevision?: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  projectId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectManagedServiceAttachmentsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  expectedContextRevision?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  projectId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectManagedServiceCatalogArgs = {
  clusterId: Scalars["GUID"]["input"];
  projectId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectManagedServicesArgs = {
  projectId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectManagedServicesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  clusterId?: InputMaybe<Scalars["GUID"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  kinds?: InputMaybe<Array<Scalars["String"]["input"]>>;
  limit?: Scalars["Int"]["input"];
  name?: InputMaybe<Scalars["String"]["input"]>;
  projectId: Scalars["GUID"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  statuses?: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type QueryAstroliftProjectResourceClustersArgs = {
  projectId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectSecretBundlesArgs = {
  projectId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectSlugAvailableArgs = {
  excludeId?: InputMaybe<Scalars["GUID"]["input"]>;
  slug: Scalars["String"]["input"];
  teamId: Scalars["GUID"]["input"];
};

export type QueryAstroliftProjectsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: InputMaybe<AstroliftListSortKey>;
};

export type QueryAstroliftProviderPluginArgs = {
  expectedId?: InputMaybe<Scalars["GUID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type QueryAstroliftProviderRegionsArgs = {
  providerPluginSlug: Scalars["String"]["input"];
};

export type QueryAstroliftQuotaUsageHistoryArgs = {
  quotaId: Scalars["GUID"]["input"];
  windowDays?: Scalars["Int"]["input"];
};

export type QueryAstroliftRecentActivityArgs = {
  cursor?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftRecentClusterWorkflowsArgs = {
  clusterId: Scalars["GUID"]["input"];
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftRenderedManifestArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  imageTag?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftRoleArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftRoleBindingsCsvArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftRoleBindingsListFilter>;
  roleId?: InputMaybe<Scalars["GUID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftRoleBindingsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftRoleBindingsListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  roleId?: InputMaybe<Scalars["GUID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftRolesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftRolesListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: InputMaybe<AstroliftListSortKey>;
};

export type QueryAstroliftRunAuditArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftRunAuditFilter>;
  first?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftScheduledJobRunArgs = {
  id: Scalars["String"]["input"];
};

export type QueryAstroliftScheduledJobRunsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftScheduledJobRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftScheduledJobRunsFilter>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftSearchableUsersArgs = {
  query: Scalars["String"]["input"];
};

export type QueryAstroliftSecretChangeProposalArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftSecretChangeProposalMetadataArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftSecretChangeProposalsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftSecretChangeProposalsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftSharedModelPromptReadinessArgs = {
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  expectedVersion: Scalars["Int"]["input"];
  id: Scalars["GUID"]["input"];
};

export type QueryAstroliftSourceConnectionsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftSourceFileArgs = {
  connectionId: Scalars["String"]["input"];
  path: Scalars["String"]["input"];
  ref: Scalars["String"]["input"];
  repoFullName: Scalars["String"]["input"];
};

export type QueryAstroliftSshDeployKeysArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftSshDeployKeysPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftTaskRunArgs = {
  id: Scalars["String"]["input"];
};

export type QueryAstroliftTaskRunsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  status?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftTaskRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftTeamMembersArgs = {
  teamId: Scalars["GUID"]["input"];
};

export type QueryAstroliftTeamSlugAvailableArgs = {
  excludeId?: InputMaybe<Scalars["GUID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type QueryAstroliftTeamsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftTeamsListFilter>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
  sortBy?: InputMaybe<AstroliftListSortKey>;
};

export type QueryAstroliftTopologyTrafficArgs = {
  appSlug: Scalars["String"]["input"];
  end: Scalars["DateTime"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  start: Scalars["DateTime"]["input"];
};

export type QueryAstroliftTraceSpansArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  traceId: Scalars["String"]["input"];
};

export type QueryAstroliftWebhookDeliveriesArgs = {
  limit?: Scalars["Int"]["input"];
  subscriptionId: Scalars["GUID"]["input"];
};

export type QueryAstroliftWebhookDeliveriesPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
  subscriptionId: Scalars["GUID"]["input"];
};

export type QueryAstroliftWebhookSubscriptionsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftWebhookSubscriptionsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftWorkflowInstanceArgs = {
  workflowId: Scalars["String"]["input"];
};

export type QueryAstroliftWorkflowInstanceDetailArgs = {
  workflowId: Scalars["String"]["input"];
};

export type QueryAstroliftWorkflowInstancesArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  status?: InputMaybe<Scalars["String"]["input"]>;
  workflowType?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftWorkflowRunsArgs = {
  limit?: Scalars["Int"]["input"];
};

export type QueryAstroliftWorkloadArgs = {
  appSlug: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
};

export type QueryAstroliftWorkloadActionTargetArgs = {
  environmentId?: InputMaybe<Scalars["GUID"]["input"]>;
  workloadId: Scalars["GUID"]["input"];
};

export type QueryAstroliftWorkloadIdentityGrantsArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  unappliedOnly?: Scalars["Boolean"]["input"];
};

export type QueryAstroliftWorkloadManifestArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  imageTag?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug: Scalars["String"]["input"];
};

export type QueryAstroliftWorkloadPodStatusBreakdownArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug: Scalars["String"]["input"];
};

export type QueryAstroliftWorkloadResourceUsageArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug: Scalars["String"]["input"];
};

export type QueryAstroliftWorkloadScalingStatusArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug: Scalars["String"]["input"];
};

export type QueryAstroliftWorkloadsArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAstroliftWorkloadsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<AstroliftWorkloadsFilter>;
  kinds?: InputMaybe<Array<Scalars["String"]["input"]>>;
  limit?: Scalars["Int"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAuditLogsArgs = {
  limit?: Scalars["Int"]["input"];
  operation?: InputMaybe<Scalars["String"]["input"]>;
  userId?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryAuditLogsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  operation?: InputMaybe<Scalars["String"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  userId?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryBriefArgs = {
  id: Scalars["ID"]["input"];
};

export type QueryClusterModelDeploymentArgs = {
  id: Scalars["GUID"]["input"];
  organizationId: Scalars["GUID"]["input"];
};

export type QueryClusterModelDeploymentsPageArgs = {
  filter?: InputMaybe<ClusterModelsFilterInput>;
  organizationId: Scalars["GUID"]["input"];
  page?: Scalars["Int"]["input"];
  pageSize?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryClusterModelPlacementClustersPageArgs = {
  organizationId: Scalars["GUID"]["input"];
  page?: Scalars["Int"]["input"];
  pageSize?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryClusterModelRuntimeAdmissionArgs = {
  input: ProvisionClusterModelInput;
};

export type QueryClusterModelSubscriptionTargetsPageArgs = {
  modelDeploymentId: Scalars["GUID"]["input"];
  organizationId: Scalars["GUID"]["input"];
  page?: Scalars["Int"]["input"];
  pageSize?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryClusterModelSubscriptionsPageArgs = {
  appEnvironmentId?: InputMaybe<Scalars["GUID"]["input"]>;
  modelDeploymentId: Scalars["GUID"]["input"];
  organizationId: Scalars["GUID"]["input"];
  page?: Scalars["Int"]["input"];
  pageSize?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryEffectivePermissionsArgs = {
  userId: Scalars["ID"]["input"];
};

export type QueryExportWorkflowManifestArgs = {
  definitionSlug: Scalars["String"]["input"];
};

export type QueryFormDefinitionArgs = {
  slug: Scalars["String"]["input"];
};

export type QueryFormDefinitionsArgs = {
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryFormSubmissionsArgs = {
  slug: Scalars["String"]["input"];
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryMembersPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryOrgSkillReposArgs = {
  orgId: Scalars["ID"]["input"];
};

export type QueryOrgToolDefsArgs = {
  orgId: Scalars["ID"]["input"];
};

export type QueryOrgToolDefsPageArgs = {
  filter?: InputMaybe<AstroliftToolDefsFilter>;
  orgId: Scalars["ID"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryOrganizationArgs = {
  id: Scalars["ID"]["input"];
};

export type QueryOrganizationsArgs = {
  query?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryOrganizationsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryPendingHumanGatesArgs = {
  limit?: Scalars["Int"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryPermissionCompareArgs = {
  scopeId?: InputMaybe<Scalars["String"]["input"]>;
  scopeType?: InputMaybe<Scalars["String"]["input"]>;
  userIdA: Scalars["ID"]["input"];
  userIdB: Scalars["ID"]["input"];
};

export type QueryPermissionDiagnoseArgs = {
  permission: Scalars["String"]["input"];
  scopeId?: InputMaybe<Scalars["String"]["input"]>;
  scopeType?: InputMaybe<Scalars["String"]["input"]>;
  userId: Scalars["ID"]["input"];
};

export type QueryPipelineJobRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  runId: Scalars["GUID"]["input"];
};

export type QueryPipelineStartRequestArgs = {
  pipelineId: Scalars["GUID"]["input"];
  requestId: Scalars["String"]["input"];
};

export type QueryPipelineStepRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  jobRunId: Scalars["GUID"]["input"];
  limit?: Scalars["Int"]["input"];
  runId: Scalars["GUID"]["input"];
};

export type QueryPreviewAstroliftDeregisterArgs = {
  appSlug: Scalars["String"]["input"];
};

export type QueryPreviewAstroliftForceRedeployArgs = {
  appSlug: Scalars["String"]["input"];
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryPreviewWorkflowManifestArgs = {
  toml: Scalars["String"]["input"];
};

export type QueryScanAgentManifestsArgs = {
  orgId: Scalars["ID"]["input"];
  ref?: Scalars["String"]["input"];
  sourceKind?: Scalars["String"]["input"];
  sourceRepo: Scalars["String"]["input"];
};

export type QueryScanAppManifestsArgs = {
  ref?: Scalars["String"]["input"];
  sourceKind?: Scalars["String"]["input"];
  sourceRepo: Scalars["String"]["input"];
};

export type QuerySkillArgs = {
  id: Scalars["ID"]["input"];
};

export type QuerySkillsArgs = {
  isGlobal?: Scalars["Boolean"]["input"];
  orgId: Scalars["ID"]["input"];
};

export type QuerySkillsPageArgs = {
  filter?: InputMaybe<AstroliftSkillsFilter>;
  orgId: Scalars["ID"]["input"];
  page?: InputMaybe<Scalars["Int"]["input"]>;
  pageSize?: InputMaybe<Scalars["Int"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryToolDefArgs = {
  id: Scalars["ID"]["input"];
};

export type QueryToolDefsArgs = {
  skillId: Scalars["ID"]["input"];
};

export type QueryWorkflowArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type QueryWorkflowDefinitionArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type QueryWorkflowDefinitionByIdArgs = {
  id: Scalars["GUID"]["input"];
};

export type QueryWorkflowDefinitionRunArgs = {
  guid: Scalars["String"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryWorkflowDefinitionRunsArgs = {
  limit?: Scalars["Int"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  projectId?: InputMaybe<Scalars["ID"]["input"]>;
  status?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryWorkflowDefinitionRunsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  filter?: InputMaybe<WorkflowDefinitionRunsFilter>;
  limit?: Scalars["Int"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
  sort?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryWorkflowDefinitionStartRequestArgs = {
  requestId: Scalars["String"]["input"];
};

export type QueryWorkflowDefinitionsArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  projectId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryWorkflowDefinitionsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  projectId?: InputMaybe<Scalars["ID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type QueryWorkflowExecutionArgs = {
  executionId: Scalars["ID"]["input"];
};

export type QueryWorkflowExecutionStagesArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  executionId: Scalars["ID"]["input"];
  limit?: Scalars["Int"]["input"];
};

export type QueryWorkflowInstanceArgs = {
  id: Scalars["ID"]["input"];
};

export type QueryWorkflowInstancesArgs = {
  modelLabel?: InputMaybe<Scalars["String"]["input"]>;
  objectId: Scalars["Int"]["input"];
};

export type QueryWorkflowRunsArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  workflowId: Scalars["ID"]["input"];
};

export type QueryWorkflowStageExecutionsArgs = {
  runId: Scalars["String"]["input"];
  workflowId: Scalars["String"]["input"];
};

export type QueryWorkflowStagesArgs = {
  workflowSlug: Scalars["String"]["input"];
};

export type QueryWorkflowsArgs = {
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
};

export type QueryWorkflowsPageArgs = {
  after?: InputMaybe<Scalars["String"]["input"]>;
  limit?: Scalars["Int"]["input"];
  orgId?: InputMaybe<Scalars["ID"]["input"]>;
  search?: InputMaybe<Scalars["String"]["input"]>;
};

export type ReapCloudOrphanInput = {
  clusterSlug: InputMaybe<Scalars["String"]["input"]>;
  forceDestroy: Scalars["Boolean"]["input"];
  kind: Scalars["String"]["input"];
  reapKey: Scalars["String"]["input"];
};

export type ReapCloudOrphanPayload = {
  alreadyGone: Scalars["Boolean"]["output"];
  identifier: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  message: Scalars["String"]["output"];
  reaped: Scalars["Boolean"]["output"];
};

export type ReapCloudOrphanPayloadMutationResult = {
  data?: Maybe<ReapCloudOrphanPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type RecheckDomainValidationInput = {
  id: Scalars["GUID"]["input"];
};

export type ReconcileClusterIngressesInput = {
  clusterId: Scalars["GUID"]["input"];
};

export type ReconcileClusterIngressesResult = {
  errors: Array<Scalars["String"]["output"]>;
  reconciledCount: Scalars["Int"]["output"];
  skippedCount: Scalars["Int"]["output"];
};

export type ReconcileClusterIngressesResultMutationResult = {
  data?: Maybe<ReconcileClusterIngressesResult>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type RecordClusterBootstrapRunInput = {
  chartVersion: Scalars["String"]["input"];
  cliVersion: Scalars["String"]["input"];
  clusterSlug: Scalars["String"]["input"];
  endedAt: Scalars["DateTime"]["input"];
  errorMessage: InputMaybe<Scalars["String"]["input"]>;
  hostInfo: Scalars["JSON"]["input"];
  installedReleases: Scalars["JSON"]["input"];
  startedAt: Scalars["DateTime"]["input"];
  status: Scalars["String"]["input"];
};

export type RefreshClusterManagementInputType = {
  clusterId: Scalars["GUID"]["input"];
  forcePreflight: Scalars["Boolean"]["input"];
};

export type RegisterAgentRepoInput = {
  defaultBranch: InputMaybe<Scalars["String"]["input"]>;
  deployBranch: InputMaybe<Scalars["String"]["input"]>;
  manifestPaths: InputMaybe<Array<Scalars["String"]["input"]>>;
  projectId: Scalars["GUID"]["input"];
  ref: Scalars["String"]["input"];
  sourceKind: Scalars["String"]["input"];
  sourceRepo: Scalars["String"]["input"];
  sourceUrl: InputMaybe<Scalars["String"]["input"]>;
};

export type RegisterAppInput = {
  approverTeamId: InputMaybe<Scalars["GUID"]["input"]>;
  approverUserIds: InputMaybe<Array<Scalars["String"]["input"]>>;
  buildArgs: InputMaybe<Scalars["JSON"]["input"]>;
  buildContext: Scalars["String"]["input"];
  buildMode: Scalars["String"]["input"];
  buildStrategy: Scalars["String"]["input"];
  cronExpression: InputMaybe<Scalars["String"]["input"]>;
  defaultBranch: InputMaybe<Scalars["String"]["input"]>;
  deployBranch: InputMaybe<Scalars["String"]["input"]>;
  description: InputMaybe<Scalars["String"]["input"]>;
  dockerfilePath: Scalars["String"]["input"];
  manifestPath: InputMaybe<Scalars["String"]["input"]>;
  manifestRaw: InputMaybe<Scalars["String"]["input"]>;
  minimumApprovals: InputMaybe<Scalars["Int"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  projectId: Scalars["GUID"]["input"];
  requiresApproval: InputMaybe<Scalars["Boolean"]["input"]>;
  slug: InputMaybe<Scalars["String"]["input"]>;
  sourceKind: Scalars["String"]["input"];
  sourceRepo: Scalars["String"]["input"];
  sourceUrl: InputMaybe<Scalars["String"]["input"]>;
  triggerMode: InputMaybe<Scalars["String"]["input"]>;
};

export type RegisterAppRepoInput = {
  defaultBranch: InputMaybe<Scalars["String"]["input"]>;
  deployBranch: InputMaybe<Scalars["String"]["input"]>;
  projectId: Scalars["GUID"]["input"];
  ref: Scalars["String"]["input"];
  sourceKind: Scalars["String"]["input"];
  sourceRepo: Scalars["String"]["input"];
  sourceUrl: InputMaybe<Scalars["String"]["input"]>;
};

export type RegisterMobileDeviceInput = {
  deviceToken: Scalars["String"]["input"];
  label: InputMaybe<Scalars["String"]["input"]>;
  platform: Scalars["String"]["input"];
};

export type RegisterOrgSkillRepoInput = {
  alias: Scalars["String"]["input"];
  defaultRef: Scalars["String"]["input"];
  displayName: InputMaybe<Scalars["String"]["input"]>;
  repoFullName: Scalars["String"]["input"];
  sourceConnectionId: InputMaybe<Scalars["GUID"]["input"]>;
  sourceKind: Scalars["String"]["input"];
};

export type RegisterTenantClusterInput = {
  authConfig: InputMaybe<Scalars["JSON"]["input"]>;
  authMethod: Scalars["String"]["input"];
  caCert: InputMaybe<Scalars["String"]["input"]>;
  endpoint: InputMaybe<Scalars["String"]["input"]>;
  ingressClass: InputMaybe<Scalars["String"]["input"]>;
  name: Scalars["String"]["input"];
  organizationScoped: Scalars["Boolean"]["input"];
  providerConfig: InputMaybe<Scalars["JSON"]["input"]>;
  providerPluginSlug: Scalars["String"]["input"];
  region: InputMaybe<Scalars["String"]["input"]>;
  slug: Scalars["String"]["input"];
};

export type ReissueManagedDomainCertPayload = {
  message: Scalars["String"]["output"];
  signaled: Scalars["Boolean"]["output"];
  zone: Scalars["String"]["output"];
};

export type ReissueManagedDomainCertPayloadMutationResult = {
  data?: Maybe<ReissueManagedDomainCertPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type RejectByTokenInput = {
  reason: InputMaybe<Scalars["String"]["input"]>;
  token: Scalars["String"]["input"];
};

export type RejectSecretChangeInput = {
  proposalId: Scalars["GUID"]["input"];
  reason: Scalars["String"]["input"];
};

export type ReleaseObservabilityRetentionHoldInput = {
  holdId: Scalars["GUID"]["input"];
};

export type RemoveAppDomainInput = {
  id: Scalars["GUID"]["input"];
};

export type RemoveEmailSuppressionEntryInput = {
  address: Scalars["String"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
};

export type RemoveOrgSkillRepoInput = {
  id: Scalars["GUID"]["input"];
};

export type RemoveOrganizationAllowlistDomainInput = {
  id: Scalars["GUID"]["input"];
};

export type ReprovisionManagedServiceInput = {
  managedServiceId: Scalars["GUID"]["input"];
};

export type RequestAttestationChallengeInput = {
  kind: Scalars["String"]["input"];
};

export type RequestQuotaIncreaseInput = {
  factor: Scalars["Float"]["input"];
  quotaId: Scalars["GUID"]["input"];
  reason: Scalars["String"]["input"];
};

export type RerunOnboardingInput = {
  appSlug: Scalars["String"]["input"];
};

export type ResendInvitationInput = {
  id: Scalars["GUID"]["input"];
};

export type RestartWorkloadInput = {
  environmentId: InputMaybe<Scalars["GUID"]["input"]>;
  expectedClusterId: InputMaybe<Scalars["GUID"]["input"]>;
  expectedNamespace: InputMaybe<Scalars["String"]["input"]>;
  ifMatchAppVersion: InputMaybe<Scalars["Int"]["input"]>;
  ifMatchClusterVersion: InputMaybe<Scalars["Int"]["input"]>;
  ifMatchEnvironmentVersion: InputMaybe<Scalars["Int"]["input"]>;
  workloadId: Scalars["GUID"]["input"];
};

export type RestoreAppInput = {
  appSlug: Scalars["String"]["input"];
};

export type ResumeAppWebhookDeploysInput = {
  appSlug: Scalars["String"]["input"];
};

export type ResyncManifestFromRepoInput = {
  appSlug: Scalars["String"]["input"];
};

export type ResyncManifestPayload = {
  envKeysChanged: Scalars["Int"]["output"];
  managedServicesAdded: Array<Scalars["String"]["output"]>;
  managedServicesRemoved: Array<Scalars["String"]["output"]>;
  schedulesChanged: Scalars["Int"]["output"];
  summary: Scalars["String"]["output"];
  syncState: Scalars["String"]["output"];
  workloadsAdded: Array<Scalars["String"]["output"]>;
  workloadsChanged: Array<Scalars["String"]["output"]>;
  workloadsRemoved: Array<Scalars["String"]["output"]>;
};

export type ResyncManifestPayloadMutationResult = {
  data?: Maybe<ResyncManifestPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type RetryAstroliftAutowireInput = {
  appSlug: Scalars["String"]["input"];
};

export type RevalidateManagedDomainPayload = {
  message: Scalars["String"]["output"];
  signaled: Scalars["Boolean"]["output"];
  zone: Scalars["String"]["output"];
};

export type RevalidateManagedDomainPayloadMutationResult = {
  data?: Maybe<RevalidateManagedDomainPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type RevealAppSecretInput = {
  appSlug: Scalars["String"]["input"];
  secretId: Scalars["String"]["input"];
};

export type RevealManagedServiceConnectionInput = {
  managedServiceId: Scalars["GUID"]["input"];
};

export type ReviewedWorkflowDefinition = {
  definition: WorkflowDefinitionSummary;
  guid: Scalars["GUID"]["output"];
  inputContract: RunInputContract;
  revision: Scalars["String"]["output"];
};

export type RevokeApiTokenInput = {
  id: Scalars["GUID"]["input"];
};

export type RevokeAppCertificateInput = {
  customDomainId: Scalars["GUID"]["input"];
};

export type RevokeAstroliftSessionInput = {
  reason: InputMaybe<Scalars["String"]["input"]>;
  sessionId: Scalars["GUID"]["input"];
};

export type RevokeDeployTokenInput = {
  id: Scalars["GUID"]["input"];
};

export type RevokeInvitationInput = {
  id: Scalars["GUID"]["input"];
};

export type RevokeMobileDeviceInput = {
  id: Scalars["GUID"]["input"];
};

export type RevokeModelSubscriptionInput = {
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  id: Scalars["GUID"]["input"];
  ifMatchDeploymentVersion: Scalars["Int"]["input"];
  ifMatchVersion: Scalars["Int"]["input"];
  organizationId: Scalars["GUID"]["input"];
};

export type RevokeRoleBindingInput = {
  id: Scalars["GUID"]["input"];
};

export type RevokeTeamAccessInput = {
  appId: Scalars["GUID"]["input"];
  teamId: Scalars["GUID"]["input"];
};

export type Revokemobiledevicepayload = {
  id: Scalars["GUID"]["output"];
  revoked: Scalars["Boolean"]["output"];
};

export type RevokemobiledevicepayloadMutationResult = {
  data?: Maybe<Revokemobiledevicepayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type RotateAppSecretInput = {
  appSlug: Scalars["String"]["input"];
  expiresAt: InputMaybe<Scalars["DateTime"]["input"]>;
  ifMatchVersion: InputMaybe<Scalars["Int"]["input"]>;
  key: Scalars["String"]["input"];
  scope: InputMaybe<Scalars["String"]["input"]>;
  setVia: InputMaybe<Scalars["String"]["input"]>;
  value: Scalars["String"]["input"];
};

export type RotateDeployTokenInput = {
  id: Scalars["GUID"]["input"];
};

export type RotateOutboundWebhookSecretInput = {
  id: Scalars["GUID"]["input"];
};

export type RotateSecretBundleInput = {
  id: Scalars["GUID"]["input"];
};

export type RotateWebhookSecretInput = {
  connectionId: Scalars["GUID"]["input"];
};

export type RotateZentinelleGatewayCredentialInput = {
  clusterId: Scalars["GUID"]["input"];
  overlapSeconds: InputMaybe<Scalars["Int"]["input"]>;
};

export type RunAstroliftAgentInput = {
  agentSlug: Scalars["String"]["input"];
  callbackMode: AgentTaskCallbackMode;
  callbackSecretRef: InputMaybe<Scalars["String"]["input"]>;
  callbackUrl: InputMaybe<Scalars["String"]["input"]>;
  clientRequestId: InputMaybe<Scalars["String"]["input"]>;
  correlationId: InputMaybe<Scalars["String"]["input"]>;
  environmentSpecId: InputMaybe<Scalars["GUID"]["input"]>;
  timeoutSeconds: InputMaybe<Scalars["Int"]["input"]>;
  triggerPayload: InputMaybe<Scalars["JSON"]["input"]>;
};

export type RunInputContract = {
  acceptsInputs: Scalars["Boolean"]["output"];
  digest: Scalars["String"]["output"];
  error: Scalars["String"]["output"];
  fields: Array<RunInputField>;
  schema?: Maybe<Scalars["JSON"]["output"]>;
  supported: Scalars["Boolean"]["output"];
  supportsSimpleForm: Scalars["Boolean"]["output"];
};

export type RunInputField = {
  constraints: Scalars["JSON"]["output"];
  default?: Maybe<Scalars["JSON"]["output"]>;
  enumValues?: Maybe<Scalars["JSON"]["output"]>;
  hasDefault: Scalars["Boolean"]["output"];
  kind: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  required: Scalars["Boolean"]["output"];
  sensitive: Scalars["Boolean"]["output"];
  simple: Scalars["Boolean"]["output"];
};

export type RunJobOnceInput = {
  appSlug: Scalars["String"]["input"];
  environmentName: Scalars["String"]["input"];
  jobSlug: Scalars["String"]["input"];
};

export type RunTaskInput = {
  appSlug: Scalars["String"]["input"];
  command: Array<Scalars["String"]["input"]>;
  environmentName: InputMaybe<Scalars["String"]["input"]>;
  workloadSlug: Scalars["String"]["input"];
};

export type RunWorkflowDefinitionResult = {
  dispatchStatus?: Maybe<Scalars["String"]["output"]>;
  errors: Array<ValidationError>;
  ok: Scalars["Boolean"]["output"];
  requestId?: Maybe<Scalars["String"]["output"]>;
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId?: Maybe<Scalars["String"]["output"]>;
  workflowRunId?: Maybe<Scalars["ID"]["output"]>;
};

export type RunWorkflowResult = {
  errors: Array<ValidationError>;
  instanceId?: Maybe<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  runId?: Maybe<Scalars["String"]["output"]>;
  workflowRunId?: Maybe<Scalars["String"]["output"]>;
};

export type ScaleWorkloadInput = {
  environmentId: InputMaybe<Scalars["GUID"]["input"]>;
  expectedClusterId: InputMaybe<Scalars["GUID"]["input"]>;
  expectedNamespace: InputMaybe<Scalars["String"]["input"]>;
  ifMatchAppVersion: InputMaybe<Scalars["Int"]["input"]>;
  ifMatchClusterVersion: InputMaybe<Scalars["Int"]["input"]>;
  ifMatchEnvironmentVersion: InputMaybe<Scalars["Int"]["input"]>;
  replicas: Scalars["Int"]["input"];
  workloadId: Scalars["GUID"]["input"];
};

export type SendManagedServiceTestEmailInput = {
  body: InputMaybe<Scalars["String"]["input"]>;
  managedServiceId: Scalars["GUID"]["input"];
  recipient: Scalars["String"]["input"];
  subject: InputMaybe<Scalars["String"]["input"]>;
};

export type SetActiveIdentityProviderInput = {
  id: Scalars["GUID"]["input"];
};

export type SetAlertSubscriptionInput = {
  alertKind: Scalars["String"]["input"];
  appSlug: Scalars["String"]["input"];
  channel: Scalars["String"]["input"];
  enabled: Scalars["Boolean"]["input"];
};

export type SetAppAccessInput = {
  appSlug: Scalars["String"]["input"];
  groups: Array<Scalars["String"]["input"]>;
  users: Array<Scalars["String"]["input"]>;
};

export type SetAppSecretInput = {
  appSlug: Scalars["String"]["input"];
  expiresAt: InputMaybe<Scalars["DateTime"]["input"]>;
  ifMatchVersion: InputMaybe<Scalars["Int"]["input"]>;
  key: Scalars["String"]["input"];
  scope: InputMaybe<Scalars["String"]["input"]>;
  setVia: InputMaybe<Scalars["String"]["input"]>;
  value: Scalars["String"]["input"];
};

export type SetAppSecretMetadataInput = {
  appSlug: Scalars["String"]["input"];
  environmentName: InputMaybe<Scalars["String"]["input"]>;
  expiresAt: InputMaybe<Scalars["DateTime"]["input"]>;
  key: Scalars["String"]["input"];
  scope: InputMaybe<Scalars["String"]["input"]>;
  setVia: InputMaybe<Scalars["String"]["input"]>;
};

export type SetAppSubdomainInput = {
  id: Scalars["GUID"]["input"];
  subdomain: Scalars["String"]["input"];
};

export type SetClusterAuthUserEnabledInput = {
  clusterId: Scalars["GUID"]["input"];
  enabled: Scalars["Boolean"]["input"];
  expectedSource: InputMaybe<ExpectedClusterAuthSourceInput>;
  expectedUserId: InputMaybe<Scalars["String"]["input"]>;
  username: Scalars["String"]["input"];
};

export type SetClusterAuthUserGroupsInput = {
  add: Array<Scalars["String"]["input"]>;
  clusterId: Scalars["GUID"]["input"];
  expectedSource: InputMaybe<ExpectedClusterAuthSourceInput>;
  expectedUserId: InputMaybe<Scalars["String"]["input"]>;
  remove: Array<Scalars["String"]["input"]>;
  username: Scalars["String"]["input"];
};

export type SetClusterAuthUserPasswordInput = {
  clusterId: Scalars["GUID"]["input"];
  expectedSource: InputMaybe<ExpectedClusterAuthSourceInput>;
  expectedUserId: InputMaybe<Scalars["String"]["input"]>;
  password: Scalars["String"]["input"];
  permanent: Scalars["Boolean"]["input"];
  username: Scalars["String"]["input"];
};

export type SetDomainPathRoutesInput = {
  domainId: Scalars["GUID"]["input"];
  routes: Array<DomainPathRouteInput>;
};

export type SetDomainRedirectsInput = {
  domainId: Scalars["GUID"]["input"];
  rules: Array<DomainRedirectRuleInput>;
};

export type SetEnvironmentSettingInput = {
  environmentId: Scalars["GUID"]["input"];
  key: Scalars["String"]["input"];
  value: Scalars["String"]["input"];
};

export type SetNotificationPreferenceInput = {
  channel: Scalars["String"]["input"];
  enabled: Scalars["Boolean"]["input"];
  eventKind: Scalars["String"]["input"];
};

export type SetNotificationProfileInput = {
  config: Scalars["JSON"]["input"];
  driver: Scalars["String"]["input"];
  retentionDeliveryDays: InputMaybe<Scalars["Int"]["input"]>;
};

export type SetOrganizationModuleInput = {
  enabled: Scalars["Boolean"]["input"];
  key: Scalars["String"]["input"];
};

export type SetPipelineSecretInput = {
  name: Scalars["String"]["input"];
  pipelineId: Scalars["GUID"]["input"];
  value: Scalars["String"]["input"];
};

export type SetPreviewPinnedInput = {
  id: Scalars["GUID"]["input"];
  pinned: Scalars["Boolean"]["input"];
  reason: InputMaybe<Scalars["String"]["input"]>;
};

export type SetRetentionPolicyInput = {
  appSlug: Scalars["String"]["input"];
  retentionDays: Scalars["Int"]["input"];
  signal: Scalars["String"]["input"];
};

export type SetZentinelleGatewayEnabledInput = {
  clusterId: Scalars["GUID"]["input"];
  enabled: Scalars["Boolean"]["input"];
};

export type SharedDirectoryType = {
  directoryCount: Scalars["Int"]["output"];
  fileCount: Scalars["Int"]["output"];
};

export type SharedModelDensityRow = {
  applied?: Maybe<ModelResourceRequests>;
  desired: ModelResourceRequests;
  name: Scalars["String"]["output"];
  observations: Array<ModelMetricObservation>;
  serviceId: Scalars["GUID"]["output"];
  status: Scalars["String"]["output"];
};

export type SkillInput = {
  content: Scalars["String"]["input"];
  dependencies: InputMaybe<Scalars["JSON"]["input"]>;
  description: Scalars["String"]["input"];
  name: Scalars["String"]["input"];
  slug: Scalars["String"]["input"];
};

export type SoftDeleteAppInput = {
  id: Scalars["GUID"]["input"];
};

export type SoftDeleteByGuidInput = {
  id: Scalars["GUID"]["input"];
};

export type SoftDeleteManagedDomainInput = {
  id: Scalars["GUID"]["input"];
};

export type Softdeletepayload = {
  deleted: Scalars["Boolean"]["output"];
  id: Scalars["GUID"]["output"];
};

export type SoftdeletepayloadMutationResult = {
  data?: Maybe<Softdeletepayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type StartDeploymentInput = {
  appSlug: Scalars["String"]["input"];
  branch: InputMaybe<Scalars["String"]["input"]>;
  ciActorKind: InputMaybe<Scalars["String"]["input"]>;
  ciProvider: InputMaybe<Scalars["String"]["input"]>;
  ciRunUrl: InputMaybe<Scalars["String"]["input"]>;
  commitAuthor: InputMaybe<Scalars["String"]["input"]>;
  commitAuthorAvatarUrl: InputMaybe<Scalars["String"]["input"]>;
  commitMessage: InputMaybe<Scalars["String"]["input"]>;
  commitSha: InputMaybe<Scalars["String"]["input"]>;
  environmentName: Scalars["String"]["input"];
  imageDigest: InputMaybe<Scalars["String"]["input"]>;
  imageTag: Scalars["String"]["input"];
  prNumber: InputMaybe<Scalars["Int"]["input"]>;
  sourceRef: InputMaybe<Scalars["String"]["input"]>;
  strategy: InputMaybe<Scalars["String"]["input"]>;
  triggerKind: Scalars["String"]["input"];
  workloadSlug: InputMaybe<Scalars["String"]["input"]>;
};

export type StartPipelineRunInput = {
  confirmed: Scalars["Boolean"]["input"];
  expectedVersion: Scalars["Int"]["input"];
  pipelineId: Scalars["GUID"]["input"];
  ref: InputMaybe<Scalars["String"]["input"]>;
  requestId: Scalars["String"]["input"];
};

export type StartWorkflowDefinitionInput = {
  confirmed: Scalars["Boolean"]["input"];
  definitionId: Scalars["GUID"]["input"];
  expectedInputSchemaDigest: Scalars["String"]["input"];
  expectedRevision: Scalars["String"]["input"];
  inputs: InputMaybe<Scalars["JSON"]["input"]>;
  requestId: Scalars["String"]["input"];
};

export type StartWorkflowResult = {
  errors: Array<ValidationError>;
  instanceId?: Maybe<Scalars["ID"]["output"]>;
  ok: Scalars["Boolean"]["output"];
};

export type SubscribeClusterModelInput = {
  alias: Scalars["String"]["input"];
  appEnvironmentId: Scalars["GUID"]["input"];
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  ifMatchEnvironmentVersion: Scalars["Int"]["input"];
  ifMatchVersion: Scalars["Int"]["input"];
  modelDeploymentId: Scalars["GUID"]["input"];
  organizationId: Scalars["GUID"]["input"];
};

export type Subscription = {
  astroliftDeploymentLifecycleStream: AstroliftDeploymentLifecycleEvent;
  astroliftOnAppLog: AstroliftAppLogLine;
  astroliftOnAppLogs: AstroliftAppLogLine;
  formSubmissionReceived: Scalars["String"]["output"];
  notificationReceived: Scalars["String"]["output"];
};

export type SubscriptionAstroliftDeploymentLifecycleStreamArgs = {
  appSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type SubscriptionAstroliftOnAppLogArgs = {
  appSlug: Scalars["String"]["input"];
  container?: InputMaybe<Scalars["String"]["input"]>;
  follow?: Scalars["Boolean"]["input"];
  podName: Scalars["String"]["input"];
  tailLines?: Scalars["Int"]["input"];
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type SubscriptionAstroliftOnAppLogsArgs = {
  appSlug: Scalars["String"]["input"];
  container?: InputMaybe<Scalars["String"]["input"]>;
  environmentName?: InputMaybe<Scalars["String"]["input"]>;
  follow?: Scalars["Boolean"]["input"];
  tailLines?: Scalars["Int"]["input"];
  workloadSlug?: InputMaybe<Scalars["String"]["input"]>;
};

export type SubscriptionFormSubmissionReceivedArgs = {
  slug: Scalars["String"]["input"];
};

export type SwitchUserResult = {
  user?: Maybe<UserType>;
};

export type SyncManifestFromRepoInput = {
  id: Scalars["GUID"]["input"];
};

export type TearDownAppInput = {
  deleteData: Scalars["Boolean"]["input"];
  forceDestroy: Scalars["Boolean"]["input"];
  id: Scalars["GUID"]["input"];
};

export type TearDownPreviewInputGql = {
  id: Scalars["GUID"]["input"];
};

export type TestModelEndpointInput = {
  managedServiceId: Scalars["GUID"]["input"];
  prompt: Scalars["String"]["input"];
};

export type TestNotificationInput = {
  id: Scalars["GUID"]["input"];
  message: InputMaybe<Scalars["String"]["input"]>;
};

export type TestSharedModelEndpointInput = {
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  expectedVersion: Scalars["Int"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  prompt: Scalars["String"]["input"];
};

export type TestWebhookInput = {
  id: Scalars["GUID"]["input"];
};

export type ToolDefInput = {
  adapter: Scalars["String"]["input"];
  description: Scalars["String"]["input"];
  handlerRef: Scalars["String"]["input"];
  implementationConfig: InputMaybe<Scalars["JSON"]["input"]>;
  inputSchema: Scalars["JSON"]["input"];
  name: Scalars["String"]["input"];
  outputSchema: Scalars["JSON"]["input"];
  slug: Scalars["String"]["input"];
};

export type TopologyEnvironmentTraffic = {
  edges: Array<TopologyTrafficEdge>;
  environmentId: Scalars["ID"]["output"];
  environmentName: Scalars["String"]["output"];
  namespace: Scalars["String"]["output"];
  reason?: Maybe<Scalars["String"]["output"]>;
  status: TopologyTrafficStatus;
  truncated: Scalars["Boolean"]["output"];
};

export type TopologyTraffic = {
  appSlug: Scalars["String"]["output"];
  edgeLimit: Scalars["Int"]["output"];
  end: Scalars["DateTime"]["output"];
  environmentLimit: Scalars["Int"]["output"];
  environments: Array<TopologyEnvironmentTraffic>;
  sampleLimit: Scalars["Int"]["output"];
  source: Scalars["String"]["output"];
  start: Scalars["DateTime"]["output"];
  status: TopologyTrafficStatus;
  stepSeconds: Scalars["Int"]["output"];
  truncated: Scalars["Boolean"]["output"];
};

export type TopologyTrafficEdge = {
  destinationWorkloadId: Scalars["ID"]["output"];
  destinationWorkloadName: Scalars["String"]["output"];
  errorRate: Scalars["Float"]["output"];
  errorRatio: Scalars["Float"]["output"];
  requestRate: Scalars["Float"]["output"];
  samples: Array<TopologyTrafficSample>;
  sourceWorkloadId: Scalars["ID"]["output"];
  sourceWorkloadName: Scalars["String"]["output"];
};

export type TopologyTrafficSample = {
  errorRate: Scalars["Float"]["output"];
  requestRate: Scalars["Float"]["output"];
  timestamp: Scalars["DateTime"]["output"];
};

export type TopologyTrafficStatus =
  | "AVAILABLE"
  | "NO_DATA"
  | "PARTIAL"
  | "UNAVAILABLE"
  | "UNCONFIGURED";

export type TransferAppInput = {
  appId: Scalars["GUID"]["input"];
  targetProjectId: InputMaybe<Scalars["GUID"]["input"]>;
  targetTeamId: InputMaybe<Scalars["GUID"]["input"]>;
};

export type TransitionLogEntry = {
  fromState: Scalars["String"]["output"];
  note: Scalars["String"]["output"];
  timestamp: Scalars["DateTime"]["output"];
  toState: Scalars["String"]["output"];
  username?: Maybe<Scalars["String"]["output"]>;
};

export type TriggerDeployWorkflowInput = {
  appSlug: Scalars["String"]["input"];
  branch: InputMaybe<Scalars["String"]["input"]>;
};

export type UnmuteAlertRuleInput = {
  ruleId: Scalars["GUID"]["input"];
};

export type UnregisterTenantClusterInput = {
  id: Scalars["GUID"]["input"];
};

export type UpdateAgentEnvironmentSpecInput = {
  agentType: InputMaybe<Scalars["String"]["input"]>;
  allowInstall: InputMaybe<Scalars["Boolean"]["input"]>;
  boxWorkspace: InputMaybe<Scalars["Boolean"]["input"]>;
  configBranch: InputMaybe<Scalars["String"]["input"]>;
  configManifestPath: InputMaybe<Scalars["String"]["input"]>;
  configRepo: InputMaybe<Scalars["String"]["input"]>;
  envVars: InputMaybe<Scalars["JSON"]["input"]>;
  gpu: InputMaybe<Scalars["Int"]["input"]>;
  gpuType: InputMaybe<Scalars["String"]["input"]>;
  imageTag: InputMaybe<Scalars["String"]["input"]>;
  managedModel: InputMaybe<Scalars["Boolean"]["input"]>;
  migProfile: InputMaybe<Scalars["String"]["input"]>;
  modelGateway: InputMaybe<Scalars["Boolean"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  runAsNonRoot: InputMaybe<Scalars["Boolean"]["input"]>;
  runtime: InputMaybe<Scalars["String"]["input"]>;
  secretRefs: InputMaybe<Scalars["JSON"]["input"]>;
  toolPreset: InputMaybe<Scalars["String"]["input"]>;
  vncEnabled: InputMaybe<Scalars["Boolean"]["input"]>;
};

export type UpdateAlertRuleInput = {
  id: Scalars["GUID"]["input"];
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  managedServiceId: InputMaybe<Scalars["GUID"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  notifyChannels: InputMaybe<Scalars["JSON"]["input"]>;
  predicate: InputMaybe<Scalars["JSON"]["input"]>;
  severity: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateAppInput = {
  approverTeamId: InputMaybe<Scalars["GUID"]["input"]>;
  approverUserIds: InputMaybe<Array<Scalars["String"]["input"]>>;
  buildArgs: InputMaybe<Scalars["JSON"]["input"]>;
  buildContext: InputMaybe<Scalars["String"]["input"]>;
  buildMode: InputMaybe<Scalars["String"]["input"]>;
  buildStrategy: InputMaybe<Scalars["String"]["input"]>;
  cronExpression: InputMaybe<Scalars["String"]["input"]>;
  cronPaused: InputMaybe<Scalars["Boolean"]["input"]>;
  defaultBranch: InputMaybe<Scalars["String"]["input"]>;
  deployBranch: InputMaybe<Scalars["String"]["input"]>;
  description: InputMaybe<Scalars["String"]["input"]>;
  dockerfilePath: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  ifMatchVersion: InputMaybe<Scalars["Int"]["input"]>;
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  manifestPath: InputMaybe<Scalars["String"]["input"]>;
  minimumApprovals: InputMaybe<Scalars["Int"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  previewEnabled: InputMaybe<Scalars["Boolean"]["input"]>;
  requiresApproval: InputMaybe<Scalars["Boolean"]["input"]>;
  sourceUrl: InputMaybe<Scalars["String"]["input"]>;
  triggerMode: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateClusterModelInput = {
  allowSubscriptions: Scalars["Boolean"]["input"];
  cpuKvCacheGiB: InputMaybe<Scalars["Int"]["input"]>;
  cpuRequest: Scalars["String"]["input"];
  expectedClusterId: Scalars["GUID"]["input"];
  expectedProviderId: Scalars["GUID"]["input"];
  gpuCount: Scalars["Int"]["input"];
  id: Scalars["GUID"]["input"];
  ifMatchVersion: Scalars["Int"]["input"];
  memoryRequest: Scalars["String"]["input"];
  organizationId: Scalars["GUID"]["input"];
};

export type UpdateEmailTemplateInput = {
  htmlBody: Scalars["String"]["input"];
  managedServiceId: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
  subject: Scalars["String"]["input"];
  textBody: Scalars["String"]["input"];
};

export type UpdateIdentityProviderInput = {
  clientId: InputMaybe<Scalars["String"]["input"]>;
  clientSecretRef: InputMaybe<Scalars["String"]["input"]>;
  config: InputMaybe<Scalars["JSON"]["input"]>;
  displayName: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  ifMatchVersion: InputMaybe<Scalars["Int"]["input"]>;
  metadataUrl: InputMaybe<Scalars["String"]["input"]>;
  oidcDiscoveryUrl: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateManagedDomainInput = {
  defaultFor: InputMaybe<Scalars["String"]["input"]>;
  dnsConfig: InputMaybe<Scalars["JSON"]["input"]>;
  id: Scalars["GUID"]["input"];
  isWildcardManaged: InputMaybe<Scalars["Boolean"]["input"]>;
};

export type UpdateManagedServiceInput = {
  config: InputMaybe<Scalars["JSON"]["input"]>;
  id: Scalars["GUID"]["input"];
  name: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateManifestInput = {
  id: Scalars["GUID"]["input"];
  rawManifest: Scalars["String"]["input"];
};

export type UpdateMyProfileInput = {
  email: InputMaybe<Scalars["String"]["input"]>;
  firstName: InputMaybe<Scalars["String"]["input"]>;
  lastName: InputMaybe<Scalars["String"]["input"]>;
  timezone: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateMyUiPreferencesInput = {
  appView: InputMaybe<Scalars["String"]["input"]>;
  appearance: InputMaybe<Scalars["JSON"]["input"]>;
  fleetView: InputMaybe<Scalars["String"]["input"]>;
  flowParticles: InputMaybe<Scalars["Boolean"]["input"]>;
  homeLayout: InputMaybe<Scalars["String"]["input"]>;
  homeLayoutAsked: InputMaybe<Scalars["Boolean"]["input"]>;
  motion: InputMaybe<Scalars["String"]["input"]>;
  restrictedSettings: InputMaybe<Scalars["String"]["input"]>;
  workflowView: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateOrgSkillRepoInput = {
  defaultRef: InputMaybe<Scalars["String"]["input"]>;
  detachSourceConnection: Scalars["Boolean"]["input"];
  displayName: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  repoFullName: InputMaybe<Scalars["String"]["input"]>;
  sourceConnectionId: InputMaybe<Scalars["GUID"]["input"]>;
  sourceKind: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateOrganizationInput = {
  allowUserProfileEdit: InputMaybe<Scalars["Boolean"]["input"]>;
  appearanceDefault: InputMaybe<Scalars["JSON"]["input"]>;
  appearanceLocked: InputMaybe<Scalars["Boolean"]["input"]>;
  auditLogRetentionDays: InputMaybe<Scalars["Int"]["input"]>;
  defaultResourceTags: InputMaybe<Scalars["JSON"]["input"]>;
  id: Scalars["GUID"]["input"];
  logRetentionDaysDefault: InputMaybe<Scalars["Int"]["input"]>;
  managedServiceIsolationPolicy: InputMaybe<Scalars["JSON"]["input"]>;
  metricsRetentionDaysDefault: InputMaybe<Scalars["Int"]["input"]>;
  metricsRollupRetentionDaysDefault: InputMaybe<Scalars["Int"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  restrictedSettingsDefault: InputMaybe<Scalars["String"]["input"]>;
  traceRetentionDaysDefault: InputMaybe<Scalars["Int"]["input"]>;
  website: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdatePipelineInput = {
  defaultBranch: InputMaybe<Scalars["String"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  repoUrl: InputMaybe<Scalars["String"]["input"]>;
  tomlPath: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdatePolicyInput = {
  actionPattern: InputMaybe<Scalars["String"]["input"]>;
  actorPattern: InputMaybe<Scalars["JSON"]["input"]>;
  conditions: InputMaybe<Scalars["JSON"]["input"]>;
  description: InputMaybe<Scalars["String"]["input"]>;
  effect: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  ifMatchVersion: InputMaybe<Scalars["Int"]["input"]>;
  name: InputMaybe<Scalars["String"]["input"]>;
  resourcePattern: InputMaybe<Scalars["JSON"]["input"]>;
};

export type UpdateProjectInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  name: InputMaybe<Scalars["String"]["input"]>;
  slug: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateProjectSecretBundleInput = {
  backendRef: Scalars["String"]["input"];
  id: Scalars["GUID"]["input"];
  name: Scalars["String"]["input"];
};

export type UpdateRoleBindingInput = {
  expiresAt: InputMaybe<Scalars["DateTime"]["input"]>;
  id: Scalars["GUID"]["input"];
  roleId: InputMaybe<Scalars["GUID"]["input"]>;
};

export type UpdateRoleInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  name: InputMaybe<Scalars["String"]["input"]>;
  permissions: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type UpdateSecurityPolicyInput = {
  appSlug: Scalars["String"]["input"];
  blockOnCriticalCves: Scalars["Boolean"]["input"];
  blockOnHighCveThreshold: InputMaybe<Scalars["Int"]["input"]>;
  blockOnMissingSignature: Scalars["Boolean"]["input"];
};

export type UpdateSourceConnectionInput = {
  appClientId: InputMaybe<Scalars["String"]["input"]>;
  displayName: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  repoVisibilityScopes: InputMaybe<Array<Scalars["String"]["input"]>>;
  rotateSecretPlaintext: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateTeamInput = {
  description: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  name: InputMaybe<Scalars["String"]["input"]>;
  slug: InputMaybe<Scalars["String"]["input"]>;
};

export type UpdateTenantClusterInput = {
  albAuthConfig: InputMaybe<Scalars["JSON"]["input"]>;
  endpoint: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  ingressClass: InputMaybe<Scalars["String"]["input"]>;
  ingressMode: InputMaybe<Scalars["String"]["input"]>;
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  oidcAuthConfig: InputMaybe<Scalars["JSON"]["input"]>;
  region: InputMaybe<Scalars["String"]["input"]>;
  syncManifests: Scalars["Boolean"]["input"];
};

export type UpdateWebhookSubscriptionInput = {
  events: InputMaybe<Array<Scalars["String"]["input"]>>;
  format: InputMaybe<Scalars["String"]["input"]>;
  id: Scalars["GUID"]["input"];
  ifMatchVersion: InputMaybe<Scalars["Int"]["input"]>;
  isActive: InputMaybe<Scalars["Boolean"]["input"]>;
  url: InputMaybe<Scalars["String"]["input"]>;
};

export type UploadCustomDomainCertificateInput = {
  certificatePem: Scalars["String"]["input"];
  id: Scalars["GUID"]["input"];
  privateKeyPem: Scalars["String"]["input"];
};

export type UploadLocation = "PUBLIC" | "STATIC";

export type UploadType = {
  contentType?: Maybe<Scalars["String"]["output"]>;
  description?: Maybe<Scalars["String"]["output"]>;
  fileUrl?: Maybe<Scalars["String"]["output"]>;
  location?: Maybe<Scalars["String"]["output"]>;
  metadata?: Maybe<Scalars["JSON"]["output"]>;
  name?: Maybe<Scalars["String"]["output"]>;
  preSignedUrl?: Maybe<Scalars["String"]["output"]>;
  /** @deprecated Use file_url instead. */
  publicPermanentUrl?: Maybe<Scalars["String"]["output"]>;
  /** @deprecated Use file_url instead. */
  publicTransientUrl?: Maybe<Scalars["String"]["output"]>;
  targetGlobalId: Scalars["String"]["output"];
};

export type UpsertOrganizationInput = {
  id: InputMaybe<Scalars["ID"]["input"]>;
  website: InputMaybe<Scalars["String"]["input"]>;
};

export type UpsertUserResult = {
  instance?: Maybe<UserType>;
};

export type UserInput = {
  firstName: InputMaybe<Scalars["String"]["input"]>;
  id: InputMaybe<Scalars["ID"]["input"]>;
  lastName: InputMaybe<Scalars["String"]["input"]>;
  profile: InputMaybe<ProfileInput>;
  username: Scalars["String"]["input"];
};

export type UserType = {
  email: Scalars["String"]["output"];
  employeeId?: Maybe<Scalars["String"]["output"]>;
  firstName: Scalars["String"]["output"];
  isAnonymous: Scalars["Boolean"]["output"];
  isNewUser: Scalars["Boolean"]["output"];
  lastName: Scalars["String"]["output"];
  memberships: Array<Scalars["JSON"]["output"]>;
  profile?: Maybe<ProfileType>;
  username: Scalars["String"]["output"];
};

export type ValidateAstroliftCiSecretsInput = {
  appSlug: Scalars["String"]["input"];
};

/** A field-level validation error. */
export type ValidationError = {
  field: Scalars["String"]["output"];
  messages: Array<Scalars["String"]["output"]>;
};

export type VerifyManagedDomainInput = {
  zone: Scalars["String"]["input"];
};

export type VerifyManagedDomainPayload = {
  message: Scalars["String"]["output"];
  verified: Scalars["Boolean"]["output"];
  zone: Scalars["String"]["output"];
};

export type VerifyManagedDomainPayloadMutationResult = {
  data?: Maybe<VerifyManagedDomainPayload>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type WebhookSecretReveal = {
  plaintextSecret: Scalars["String"]["output"];
  subscription: AstroliftWebhookSubscription;
};

export type WebhookSecretRevealMutationResult = {
  data?: Maybe<WebhookSecretReveal>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type WithdrawSecretChangeInput = {
  proposalId: Scalars["GUID"]["input"];
};

export type WorkflowDefinitionRun = {
  childRunCount: Scalars["Int"]["output"];
  currentStageOrder?: Maybe<Scalars["Int"]["output"]>;
  currentStageRole: Scalars["String"]["output"];
  definitionGuid: Scalars["String"]["output"];
  definitionName: Scalars["String"]["output"];
  definitionSlug: Scalars["String"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  guid: Scalars["String"]["output"];
  nestingDepth: Scalars["Int"]["output"];
  parentRunGuid?: Maybe<Scalars["String"]["output"]>;
  parentStageExecutionGuid?: Maybe<Scalars["String"]["output"]>;
  projectGuid?: Maybe<Scalars["String"]["output"]>;
  projectSlug: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId: Scalars["String"]["output"];
  triggerKind: Scalars["String"]["output"];
  triggeredByMe: Scalars["Boolean"]["output"];
  triggeredByUserId?: Maybe<Scalars["String"]["output"]>;
};

/** One page of a cursor-paginated or numbered list. */
export type WorkflowDefinitionRunPage = {
  items: Array<WorkflowDefinitionRun>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type WorkflowDefinitionRunsFilter = {
  /** Definition slugs. */
  definition: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Project slugs. */
  project: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** Initiator user ids (as on the row), or "me". */
  startedBy: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** true: runs the viewer started. false: runs someone or something else did. */
  startedByMe: InputMaybe<Scalars["Boolean"]["input"]>;
  /** Run statuses, any of. */
  status: InputMaybe<Array<Scalars["String"]["input"]>>;
  /** manual, api, schedule, webhook, parent or unknown. */
  trigger: InputMaybe<Array<Scalars["String"]["input"]>>;
};

export type WorkflowDefinitionStart = {
  definitionId: Scalars["GUID"]["output"];
  definitionRevision: Scalars["String"]["output"];
  dispatchLastError?: Maybe<Scalars["String"]["output"]>;
  dispatchStatus: Scalars["String"]["output"];
  executionId: Scalars["GUID"]["output"];
  id: Scalars["GUID"]["output"];
  inputSchemaDigest: Scalars["String"]["output"];
  organizationId: Scalars["GUID"]["output"];
  requestId: Scalars["String"]["output"];
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId: Scalars["String"]["output"];
};

export type WorkflowDefinitionStartMutationResult = {
  data?: Maybe<WorkflowDefinitionStart>;
  errors: Array<MutationError>;
  ok: Scalars["Boolean"]["output"];
};

export type WorkflowDefinitionSummary = {
  createdAt: Scalars["DateTime"]["output"];
  description: Scalars["String"]["output"];
  guid: Scalars["String"]["output"];
  isEnabled: Scalars["Boolean"]["output"];
  isGlobal: Scalars["Boolean"]["output"];
  name: Scalars["String"]["output"];
  organizationGuid?: Maybe<Scalars["String"]["output"]>;
  patternKind: Scalars["String"]["output"];
  projectGuid?: Maybe<Scalars["String"]["output"]>;
  projectSlug: Scalars["String"]["output"];
  projectTeamSlug: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  sourcePath: Scalars["String"]["output"];
  sourceRef: Scalars["String"]["output"];
  sourceRepo: Scalars["String"]["output"];
  stageCount: Scalars["Int"]["output"];
  stages: Array<WorkflowTopologyStage>;
};

/** One page of a cursor-paginated or numbered list. */
export type WorkflowDefinitionSummaryPage = {
  items: Array<WorkflowDefinitionSummary>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type WorkflowDefinitionType = {
  activeInstanceCount: Scalars["Int"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  description?: Maybe<Scalars["String"]["output"]>;
  instanceCount: Scalars["Int"]["output"];
  isEnabled: Scalars["Boolean"]["output"];
  modelLabel: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  organizationGuid?: Maybe<Scalars["String"]["output"]>;
  patternKind: Scalars["String"]["output"];
  projectGuid?: Maybe<Scalars["String"]["output"]>;
  projectSlug: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
  sourcePath: Scalars["String"]["output"];
  sourceRef: Scalars["String"]["output"];
  sourceRepo: Scalars["String"]["output"];
  states: Scalars["JSON"]["output"];
  transitions: Scalars["JSON"]["output"];
  workflowStages: Array<WorkflowStageType>;
};

export type WorkflowExecution = {
  definitionSlug: Scalars["String"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  failure?: Maybe<Scalars["JSON"]["output"]>;
  guid: Scalars["String"]["output"];
  isTerminal: Scalars["Boolean"]["output"];
  observationError: Scalars["String"]["output"];
  organizationGuid: Scalars["String"]["output"];
  recordId: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
  taskCleanup: Scalars["JSON"]["output"];
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId: Scalars["String"]["output"];
};

export type WorkflowExecutionControlResult = {
  errors: Array<ValidationError>;
  execution?: Maybe<WorkflowExecution>;
  ok: Scalars["Boolean"]["output"];
  requested: Scalars["Boolean"]["output"];
};

export type WorkflowExecutionStages = {
  executionGuid: Scalars["String"]["output"];
  organizationGuid: Scalars["String"]["output"];
  recordId: Scalars["String"]["output"];
  stages: WorkflowStageExecutionTypePage;
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId: Scalars["String"]["output"];
};

export type WorkflowInstanceType = {
  availableTransitions: Array<AvailableTransition>;
  completedAt?: Maybe<Scalars["DateTime"]["output"]>;
  currentState: Scalars["String"]["output"];
  history: Array<TransitionLogEntry>;
  isCompleted: Scalars["Boolean"]["output"];
  objectId?: Maybe<Scalars["Int"]["output"]>;
  startedAt: Scalars["DateTime"]["output"];
  stateLabel: Scalars["String"]["output"];
  workflowName: Scalars["String"]["output"];
  workflowSlug: Scalars["String"]["output"];
};

export type WorkflowManifestDefinitionType = {
  description: Scalars["String"]["output"];
  name: Scalars["String"]["output"];
  pattern: Scalars["String"]["output"];
  slug: Scalars["String"]["output"];
};

export type WorkflowManifestExportType = {
  error?: Maybe<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  toml?: Maybe<Scalars["String"]["output"]>;
};

export type WorkflowManifestPreviewType = {
  definition?: Maybe<WorkflowManifestDefinitionType>;
  error?: Maybe<Scalars["String"]["output"]>;
  errorColumn?: Maybe<Scalars["Int"]["output"]>;
  errorLine?: Maybe<Scalars["Int"]["output"]>;
  errorPath?: Maybe<Scalars["String"]["output"]>;
  ok: Scalars["Boolean"]["output"];
  stages: Array<WorkflowManifestStageType>;
};

export type WorkflowManifestStageType = {
  agent?: Maybe<Scalars["String"]["output"]>;
  approvers: Array<Scalars["String"]["output"]>;
  environmentSpecSlug?: Maybe<Scalars["String"]["output"]>;
  fanOut: Scalars["String"]["output"];
  kind: Scalars["String"]["output"];
  onFailure: Scalars["String"]["output"];
  order: Scalars["Int"]["output"];
  outputKey?: Maybe<Scalars["String"]["output"]>;
  prompt?: Maybe<Scalars["String"]["output"]>;
  role: Scalars["String"]["output"];
  skills: Array<Scalars["String"]["output"]>;
  timeout: Scalars["Int"]["output"];
  workflow?: Maybe<Scalars["String"]["output"]>;
};

export type WorkflowRun = {
  completedAt?: Maybe<Scalars["DateTime"]["output"]>;
  currentState: Scalars["String"]["output"];
  guid: Scalars["String"]["output"];
  isCompleted: Scalars["Boolean"]["output"];
  startedAt: Scalars["DateTime"]["output"];
  temporalRunId?: Maybe<Scalars["String"]["output"]>;
  temporalWorkflowId?: Maybe<Scalars["String"]["output"]>;
};

export type WorkflowStageExecutionType = {
  agentRunGuid?: Maybe<Scalars["String"]["output"]>;
  attemptNumber: Scalars["Int"]["output"];
  childWorkflowDefinitionSlug?: Maybe<Scalars["String"]["output"]>;
  childWorkflowRunGuid?: Maybe<Scalars["String"]["output"]>;
  childWorkflowStatus?: Maybe<Scalars["String"]["output"]>;
  createdAt: Scalars["DateTime"]["output"];
  endedAt?: Maybe<Scalars["DateTime"]["output"]>;
  errorMessage: Scalars["String"]["output"];
  executionId: Scalars["String"]["output"];
  failure?: Maybe<Scalars["JSON"]["output"]>;
  guid: Scalars["ID"]["output"];
  humanGateNote: Scalars["String"]["output"];
  humanGateState: Scalars["String"]["output"];
  output?: Maybe<Scalars["JSON"]["output"]>;
  stageApprovers: Array<Scalars["String"]["output"]>;
  stageGuid: Scalars["String"]["output"];
  stageKind: Scalars["String"]["output"];
  stageOrder: Scalars["Int"]["output"];
  stageRole: Scalars["String"]["output"];
  startedAt?: Maybe<Scalars["DateTime"]["output"]>;
  status: Scalars["String"]["output"];
};

/** One page of a cursor-paginated or numbered list. */
export type WorkflowStageExecutionTypePage = {
  items: Array<WorkflowStageExecutionType>;
  /** Opaque token for the next page; null when the list is exhausted. */
  nextCursor?: Maybe<Scalars["String"]["output"]>;
  /** The 1-based page number on a numbered page; null on a cursor page. */
  page?: Maybe<Scalars["Int"]["output"]>;
  /** Rows per page on a numbered page; null on a cursor page. */
  pageSize?: Maybe<Scalars["Int"]["output"]>;
  /** Total rows matching the filters, across all pages. */
  totalCount?: Maybe<Scalars["Int"]["output"]>;
};

export type WorkflowStageType = {
  agentDefinitionGuid?: Maybe<Scalars["String"]["output"]>;
  agentDefinitionName?: Maybe<Scalars["String"]["output"]>;
  agentRef: Scalars["String"]["output"];
  approvers: Scalars["JSON"]["output"];
  createdAt: Scalars["DateTime"]["output"];
  environmentSpecSlug: Scalars["String"]["output"];
  fanOutCount?: Maybe<Scalars["Int"]["output"]>;
  guid: Scalars["ID"]["output"];
  kind: Scalars["String"]["output"];
  onFailure: Scalars["String"]["output"];
  order: Scalars["Int"]["output"];
  outputKey: Scalars["String"]["output"];
  prompt: Scalars["String"]["output"];
  role: Scalars["String"]["output"];
  skillRefs: Scalars["JSON"]["output"];
  timeoutSeconds: Scalars["Int"]["output"];
  workflowRef: Scalars["String"]["output"];
};

export type WorkflowTopologyStage = {
  agentGuid?: Maybe<Scalars["String"]["output"]>;
  agentName: Scalars["String"]["output"];
  agentRef: Scalars["String"]["output"];
  agentSlug: Scalars["String"]["output"];
  environmentSpecSlug: Scalars["String"]["output"];
  fanOutCount?: Maybe<Scalars["Int"]["output"]>;
  fanOutDynamic: Scalars["Boolean"]["output"];
  guid: Scalars["String"]["output"];
  hasPrompt: Scalars["Boolean"]["output"];
  kind: Scalars["String"]["output"];
  onFailure: Scalars["String"]["output"];
  order: Scalars["Int"]["output"];
  outputKey: Scalars["String"]["output"];
  resolvedModel: Scalars["String"]["output"];
  role: Scalars["String"]["output"];
  skillRefs: Array<Scalars["String"]["output"]>;
  timeoutSeconds: Scalars["Int"]["output"];
  workflowRef: Scalars["String"]["output"];
};

export type ZentinelleClusterInput = {
  clusterId: Scalars["GUID"]["input"];
};
