import type {
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";
import type {
  AstroliftManagedService,
  AstroliftManagedServiceCatalogEntry,
  AstroliftProjectSecretBundle,
} from "@/graphql/services/services.types";
import type {
  WorkflowDefinitionRun,
  WorkflowDefinitionSummary,
  WorkflowTopologyStage,
} from "@/graphql/workflows/tiered.types";

import type { ProjectDetailScreenProps } from "./ProjectDetailScreen";
import type { ProjectResourcesScreenProps } from "./ProjectResourcesScreen";
import type {
  ProjectDetailApp,
  ProjectDetailMember,
  ProjectDetailProject,
} from "./use-project-detail";
import type {
  ManagedServiceCostPreview,
  ProjectAppEnvironment,
  ProjectResourceCluster,
} from "./use-project-resources";

/** Hand-typed fixtures for the project overview and project resources screens. */

const noop = () => {};
const asyncNoop = async () => {};
const yes = async () => true;

/** The JSON scalar is typed Record<string, unknown>; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

export const LONG =
  "platform-engineering-shared-production-workloads-us-west-2-with-a-deliberately-long-name";

// ─── project overview ────────────────────────────────────────────────

export const PROJECT: ProjectDetailProject = {
  id: "project-1",
  slug: "emr-bug-triage",
  name: "EMR Bug Triage",
  createdAt: "2026-08-01T12:00:00Z",
  organization: { id: "org-1", slug: "steadymd", name: "SteadyMD" },
  team: { id: "team-1", slug: "engineering", name: "Engineering" },
};

function agent(
  slug: string,
  name: string,
  overrides: Partial<AstroliftAgentListItem> = {}
): AstroliftAgentListItem {
  return {
    id: `workload-${slug}`,
    name,
    slug,
    appSlug: slug,
    projectSlug: PROJECT.slug,
    sourceRepo: "steadymd/smd-agents",
    sourceUrl: "https://github.com/steadymd/smd-agents",
    runFamily: "task",
    runMode: "once",
    runPaused: false,
    runCronExpression: "",
    lastRunStatus: "completed",
    lastRunAt: "2026-09-27T14:00:00Z",
    runningCount: 0,
    ...overrides,
  };
}

export const AGENTS: AstroliftAgentListItem[] = [
  agent("emr-triage-intake", "EMR Triage Intake"),
  agent("emr-triage-decide", "EMR Triage Decide", {
    runMode: "schedule",
    runCronExpression: "0 * * * *",
    lastRunStatus: "failed",
  }),
  agent("emr-bug-digest", "EMR Bug Digest", {
    runFamily: "service",
    runMode: "service",
    runPaused: true,
    sourceUrl: "",
    lastRunStatus: null,
    lastRunAt: null,
  }),
  agent("emr-local-probe", "EMR Local Probe", {
    sourceRepo: "",
    sourceUrl: "",
    lastRunStatus: null,
    lastRunAt: null,
  }),
];

export const LIVE_AGENTS: AstroliftAgentLiveStatus[] = [
  {
    workloadId: "workload-emr-triage-intake",
    workloadSlug: "emr-triage-intake",
    appSlug: "emr-triage-intake",
    runFamily: "task",
    runMode: "once",
    isPaused: false,
    isIdle: false,
    runningCount: 2,
    lastRunStatus: "running",
    lastRunAt: "2026-09-28T10:00:00Z",
    nextScheduledAt: null,
  },
  {
    workloadId: "workload-emr-triage-decide",
    workloadSlug: "emr-triage-decide",
    appSlug: "emr-triage-decide",
    runFamily: "task",
    runMode: "schedule",
    isPaused: false,
    isIdle: true,
    runningCount: 0,
    lastRunStatus: "failed",
    lastRunAt: "2026-09-28T09:00:00Z",
    nextScheduledAt: "2026-09-28T11:00:00Z",
  },
];

function stage(order: number, role: string, agentSlug: string, agentName: string) {
  const row: WorkflowTopologyStage = {
    guid: `stage-${order}`,
    order,
    kind: "agent_dispatch",
    role,
    agentRef: agentSlug,
    workflowRef: "",
    agentGuid: `workload-${agentSlug}`,
    agentName,
    agentSlug,
    environmentSpecSlug: agentSlug,
    resolvedModel: "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    hasPrompt: true,
    outputKey: role,
    skillRefs: [],
    fanOutCount: null,
    fanOutDynamic: false,
    onFailure: "fail",
    timeoutSeconds: 300,
  };
  return row;
}

export const WORKFLOWS: WorkflowDefinitionSummary[] = [
  {
    guid: "workflow-1",
    name: "EMR triage: code research and review",
    slug: "emr-triage",
    description: "",
    patternKind: "chained",
    isEnabled: true,
    isGlobal: false,
    organizationGuid: "org-1",
    projectGuid: PROJECT.id,
    projectSlug: PROJECT.slug,
    projectTeamSlug: PROJECT.team.slug,
    sourceRepo: "steadymd/smd-agents",
    sourcePath: "workflows/emr-triage.toml",
    sourceRef: "main",
    stageCount: 2,
    createdAt: "2026-08-13T12:00:00Z",
    stages: [
      stage(0, "intake", "emr-triage-intake", "EMR Triage Intake"),
      stage(1, "decide", "emr-triage-decide", "EMR Triage Decide"),
    ],
  },
];

export const WORKFLOW_RUNS: WorkflowDefinitionRun[] = [
  {
    guid: "run-1",
    definitionGuid: "workflow-1",
    definitionSlug: "emr-triage",
    definitionName: "EMR triage: code research and review",
    projectGuid: PROJECT.id,
    projectSlug: PROJECT.slug,
    status: "running",
    temporalWorkflowId: "wf-emr-triage-1",
    temporalRunId: "run-abc",
    currentStageOrder: 1,
    currentStageRole: "decide",
    parentRunGuid: null,
    parentStageExecutionGuid: null,
    nestingDepth: 0,
    childRunCount: 0,
    startedAt: "2026-09-28T10:00:00Z",
    endedAt: null,
  },
];

export const APPS: ProjectDetailApp[] = [
  {
    id: "app-1",
    slug: "triage-api",
    name: "Triage API",
    provisioningStatus: "ready",
    sourceRepo: "steadymd/triage-api",
    createdAt: "2026-08-02T12:00:00Z",
  },
  {
    id: "app-2",
    slug: "triage-web",
    name: "Triage Web",
    provisioningStatus: "failed",
    sourceRepo: "",
    createdAt: "2026-08-03T12:00:00Z",
  },
  {
    id: "app-3",
    slug: "triage-worker",
    name: "Triage Worker",
    provisioningStatus: "provisioning",
    sourceRepo: "steadymd/triage-worker",
    createdAt: "2026-08-04T12:00:00Z",
  },
];

export const MEMBERS: ProjectDetailMember[] = [
  {
    id: "member-1",
    joinedAt: "2026-08-05T12:00:00Z",
    scopeKind: "PROJECT",
    user: { username: "leo", email: "leo@example.com" },
  },
  {
    id: "member-2",
    joinedAt: null,
    scopeKind: "PROJECT",
    user: { username: "", email: "reviewer@example.com" },
  },
];

/** Mixed project: apps, agents, a workflow, members. */
export const DETAIL: ProjectDetailScreenProps = {
  slug: PROJECT.slug,
  project: PROJECT,
  loading: false,
  modulesLoading: false,
  canViewApps: true,
  canCreateApp: true,
  canViewAgents: true,
  canCreateAgent: true,
  canViewWorkflows: true,
  canManageMembers: true,
  apps: APPS,
  appsLoading: false,
  appsLoaded: true,
  appsError: false,
  agents: AGENTS,
  agentsLoading: false,
  agentsLoaded: true,
  agentsError: false,
  liveAgents: LIVE_AGENTS,
  workflows: WORKFLOWS,
  workflowsLoading: false,
  workflowsLoaded: true,
  workflowsError: false,
  workflowRuns: WORKFLOW_RUNS,
  directMembers: MEMBERS,
  membersLoading: false,
  resourceCount: 3,
  resourcesLoading: false,
  deleting: false,
  onDelete: asyncNoop,
};

export const DETAIL_EMPTY: ProjectDetailScreenProps = {
  ...DETAIL,
  apps: [],
  agents: [],
  liveAgents: [],
  workflows: [],
  workflowRuns: [],
  directMembers: [],
  resourceCount: 0,
};

// ─── project resources ───────────────────────────────────────────────

export const CLUSTERS: ProjectResourceCluster[] = [
  { id: "cluster-1", slug: "production", name: "Production", region: "us-east-1" },
  { id: "cluster-2", slug: "staging", name: "Staging", region: "us-west-2" },
];

export const CATALOG: AstroliftManagedServiceCatalogEntry[] = [
  {
    id: "aws:postgres:rds",
    providerPluginSlug: "aws",
    kind: "postgres",
    variant: "rds",
    displayName: "Amazon RDS for PostgreSQL",
    description: "A managed PostgreSQL instance on Amazon RDS.",
    status: "ga",
    available: true,
    unavailableReason: "",
    isDefaultForKind: true,
    sizeOptions: ["small", "medium", "large", "xlarge", "custom"],
    configSchema: {
      type: "object",
      properties: {
        engine_version: {
          type: "string",
          enum: ["15", "16", "17"],
          description: "PostgreSQL major version.",
        },
        allocated_storage_gb: { type: "integer", minimum: 20, maximum: 1000, default: 20 },
        multi_az: { type: "boolean", description: "Run a standby in a second zone." },
      },
    },
    bindingEnvs: ["DATABASE_URL"],
    issueUrl: "",
  },
  {
    id: "aws:postgres:aurora_postgres_serverless_v2",
    providerPluginSlug: "aws",
    kind: "postgres",
    variant: "aurora_postgres_serverless_v2",
    displayName: "Amazon Aurora PostgreSQL Serverless v2 cluster",
    description: "Amazon Aurora PostgreSQL Serverless v2 cluster",
    status: "planned",
    available: false,
    unavailableReason: "Driver is a non-provisioning stub or planned capability.",
    isDefaultForKind: false,
    sizeOptions: [],
    configSchema: { type: "object", properties: {} },
    bindingEnvs: [],
    issueUrl: "https://github.com/calliopeai/astrolift-app/issues/1283",
  },
];

function service(overrides: Partial<AstroliftManagedService>): AstroliftManagedService {
  return {
    id: "service-1",
    name: "triage-db",
    kind: "postgres",
    variant: "rds",
    status: "active",
    statusError: "",
    operationKind: "",
    operationWorkflowId: "",
    operationRunId: "",
    operationStartedAt: null,
    operationCompletedAt: null,
    environmentName: "production",
    registeredAppSlug: "",
    projectSlug: PROJECT.slug,
    ownerScope: "project",
    clusterSlug: "production",
    providerPortalUrl: "",
    createdAt: "2026-08-14T00:00:00Z",
    updatedAt: "2026-08-14T00:00:00Z",
    lastActionAt: null,
    lastActionKind: "",
    editableFields: [],
    attachments: [],
    volumeBindings: [],
    config: json({}),
    appliedConfig: null,
    ...overrides,
  };
}

export const SERVICES: AstroliftManagedService[] = [
  service({
    id: "service-1",
    name: "triage-db",
    operationKind: "provision",
    operationWorkflowId: "managed-service-provision-service-1",
    operationRunId: "0199a1b2-run",
    operationCompletedAt: "2026-08-14T00:05:00Z",
    providerPortalUrl: "https://console.aws.amazon.com/rds/home",
    attachments: [
      {
        id: "attachment-1",
        consumerKind: "agent",
        consumerSlug: "emr-triage-intake",
        environmentName: "production",
      },
      {
        id: "attachment-2",
        consumerKind: "app",
        consumerSlug: "triage-api",
        environmentName: "production",
      },
    ],
  }),
  service({
    id: "service-2",
    name: "agent-workspace",
    kind: "filesystem",
    variant: "rook_cephfs",
    environmentName: "shared",
    volumeBindings: [
      {
        id: "binding-1",
        name: "agent-workspace",
        mountPath: "/workspace",
        subPath: "",
        sourceKind: "dynamic_pvc",
        protocol: "cephfs",
        claimName: "",
        claimNamespace: "",
        storageClassName: "rook-cephfs",
        csiDriver: "rook-ceph.cephfs.csi.ceph.com",
        readOnly: false,
        capacity: "100Gi",
        accessModes: ["ReadWriteMany"],
        workloadNames: [],
        containerNames: [],
        credentialReferenceCount: 0,
      },
      {
        id: "binding-2",
        name: "reference-data",
        mountPath: "/reference",
        subPath: "",
        sourceKind: "static_pvc",
        protocol: "nfs",
        claimName: "reference-data",
        claimNamespace: "astrolift-shared",
        storageClassName: "",
        csiDriver: "",
        readOnly: true,
        capacity: "10Gi",
        accessModes: ["ReadOnlyMany"],
        workloadNames: [],
        containerNames: [],
        credentialReferenceCount: 2,
      },
    ],
  }),
  service({
    id: "service-3",
    name: "triage-cache",
    kind: "redis_cache",
    variant: "elasticache",
    status: "failed",
    statusError: "ElastiCache rejected the subnet group: no subnets in us-east-1e.",
    operationKind: "provision",
    operationWorkflowId: "managed-service-provision-service-3",
  }),
];

export const COST_PREVIEWS: Record<string, ManagedServiceCostPreview> = {
  "service-1": {
    managedServiceId: "service-1",
    available: true,
    reason: "",
    message: "",
    monthlyTotal: 47.45,
    currency: "USD",
    pricingSourceUrl: "https://aws.amazon.com/rds/postgresql/pricing/",
    pricingFetchedAt: "2026-09-27T00:00:00Z",
    notes: ["On-demand pricing for db.t4g.medium, single AZ, 20 GB gp3."],
    approximate: true,
  },
  "service-3": {
    managedServiceId: "service-3",
    available: false,
    reason: "unsupported_kind",
    message: "No pricing source for ElastiCache yet.",
    monthlyTotal: null,
    currency: "USD",
    pricingSourceUrl: "",
    pricingFetchedAt: "",
    notes: [],
    approximate: false,
  },
};

export const BUNDLES: AstroliftProjectSecretBundle[] = [
  {
    id: "bundle-1",
    slug: "jira-credentials",
    name: "Jira credentials",
    backendRef: "astrolift/projects/emr-bug-triage/jira-credentials",
    projectSlug: PROJECT.slug,
    clusterSlug: "production",
    keyCount: 2,
    keyNames: ["JIRA_URL", "JIRA_TOKEN"],
    lastKnownKeysAt: "2026-09-27T12:00:00Z",
    createdAt: "2026-08-20T12:00:00Z",
    consumers: [
      {
        id: "consumer-1",
        consumerKind: "agent",
        consumerSlug: "emr-triage-intake",
        environmentName: "production",
      },
    ],
  },
  {
    id: "bundle-2",
    slug: "github-app",
    name: "GitHub app",
    backendRef: "astrolift/projects/emr-bug-triage/github-app",
    projectSlug: PROJECT.slug,
    clusterSlug: "production",
    keyCount: 0,
    keyNames: [],
    lastKnownKeysAt: null,
    createdAt: "2026-08-21T12:00:00Z",
    consumers: [],
  },
];

export const APP_ENVIRONMENTS: ProjectAppEnvironment[] = [
  { id: "env-1", name: "production", registeredAppSlug: "triage-api", clusterSlug: "production" },
  { id: "env-2", name: "staging", registeredAppSlug: "triage-api", clusterSlug: "staging" },
  { id: "env-3", name: "production", registeredAppSlug: "triage-web", clusterSlug: "production" },
];

export const RESOURCES: ProjectResourcesScreenProps = {
  slug: PROJECT.slug,
  project: { id: PROJECT.id, slug: PROJECT.slug, name: PROJECT.name },
  loading: false,
  canUpdate: true,
  canWriteSecrets: true,
  canReadSecrets: true,
  clusters: CLUSTERS,
  services: SERVICES,
  bundles: BUNDLES,
  resourcesLoading: false,
  agents: AGENTS.map(({ id, slug, name }) => ({ id, slug, name })),
  projectAppEnvironments: APP_ENVIRONMENTS,
  effectiveClusterId: "cluster-1",
  effectiveClusterSlug: "production",
  onClusterChange: noop,
  catalogEntries: CATALOG,
  catalogLoading: false,
  catalogError: false,
  costPreviews: COST_PREVIEWS,
  costPreviewLoading: false,
  onCostPreview: asyncNoop,
  revealed: { "bundle-1:JIRA_URL": "https://steadymd.atlassian.net" },
  onToggleReveal: asyncNoop,
  consumerService: null,
  setConsumerService: noop,
  consumerBundle: null,
  setConsumerBundle: noop,
  provisioning: false,
  onProvision: yes,
  onReprovision: asyncNoop,
  onDeprovision: asyncNoop,
  attachingConsumer: false,
  detachingConsumer: false,
  onAttachServiceConsumer: asyncNoop,
  onDetachServiceConsumer: asyncNoop,
  creatingBundle: false,
  onCreateBundle: yes,
  onSetBundleKey: yes,
  onDeleteBundleKey: asyncNoop,
  onDeleteBundle: asyncNoop,
  attachingAgentBundle: false,
  attachingAppBundle: false,
  detachingBundleConsumer: false,
  onAttachBundleToAgent: asyncNoop,
  onAttachBundleToApp: asyncNoop,
  onDetachBundleConsumer: asyncNoop,
};

export const RESOURCES_EMPTY: ProjectResourcesScreenProps = {
  ...RESOURCES,
  services: [],
  bundles: [],
  costPreviews: {},
  revealed: {},
};
