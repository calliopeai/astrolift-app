/** Hand-typed fixtures for the Zentinelle integration screen (#1888), one per state. */
import type { ZentinelleScreenProps } from "./ZentinelleScreen";
import {
  ALL_SCOPE,
  usageTotals,
  type ZentinelleAccess,
  type ZentinelleAuditEvent,
  type ZentinelleCluster,
  type ZentinelleConnection,
  type ZentinelleHealthCounts,
  type ZentinellePolicy,
  type ZentinelleUsageRow,
} from "./zentinelle-model";

const ok = async () => null;

/** "Last seen" relative to when the story loads, so it reads the same every run. */
const ago = (minutes: number) => new Date(Date.now() - minutes * 60_000).toISOString();

export const FULL_ACCESS: ZentinelleAccess = {
  status: true,
  connect: true,
  gatewayManage: true,
  usage: true,
  policies: true,
  audit: true,
  auditExport: true,
};

export const NO_ACCESS: ZentinelleAccess = {
  status: false,
  connect: false,
  gatewayManage: false,
  usage: false,
  policies: false,
  audit: false,
  auditExport: false,
};

/** A developer: usage and policies on their projects, nothing to manage. */
export const DEVELOPER_ACCESS: ZentinelleAccess = { ...NO_ACCESS, usage: true, policies: true };

const cluster = (over: Partial<ZentinelleCluster>): ZentinelleCluster => ({
  clusterId: "0199a1b2-0000-7000-8000-000000000001",
  clusterSlug: "prod-us-east-1",
  zentinelleClusterId: "0199a1b2-0000-7000-8000-000000000001",
  gatewayName: "zentinelle-gateway",
  gatewayEnabled: true,
  gatewayDeployed: true,
  status: "deployed",
  registeredAt: "2026-10-01T14:20:00Z",
  credentialRotatedAt: "2026-10-12T09:00:00Z",
  lastError: "",
  unregistered: false,
  gatewayVersion: "0.9.1",
  lastSeenAt: ago(1),
  ...over,
});

export const HEALTHY_CONNECTION: ZentinelleConnection = {
  id: "0199a1b2-0000-7000-8000-0000000000aa",
  baseUrl: "https://zentinelle.example.com",
  gatewayUrl: "",
  status: "connected",
  zentinelleInstallId: "inst_7f3a2c",
  tenantIds: ["acme"],
  connectedAt: "2026-10-01T14:00:00Z",
  disconnectedAt: null,
  lastError: "",
  gatewayFeatureEnabled: true,
  serverVersion: "1.5.0",
  clusters: [
    cluster({}),
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000002",
      clusterSlug: "prod-eu-west-1",
      zentinelleClusterId: "0199a1b2-0000-7000-8000-000000000002",
    }),
  ],
};

/** The install connection shared clusters register through (#1905). */
export const INSTALL_CONNECTION: ZentinelleConnection = {
  ...HEALTHY_CONNECTION,
  id: "0199a1b2-0000-7000-8000-0000000000bb",
  tenantIds: [],
  clusters: [
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000020",
      clusterSlug: "platform-us-west-2",
      zentinelleClusterId: "0199a1b2-0000-7000-8000-000000000020",
    }),
  ],
};

export const DEGRADED_CONNECTION: ZentinelleConnection = {
  ...HEALTHY_CONNECTION,
  gatewayUrl: "http://zentinelle.zentinelle.svc:8000",
  clusters: [
    cluster({}),
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000002",
      clusterSlug: "prod-eu-west-1",
      status: "error",
      gatewayDeployed: false,
      lastError: "gateway Deployment did not become ready within 120s (ImagePullBackOff)",
    }),
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000003",
      clusterSlug: "staging",
      status: "registered",
      gatewayDeployed: false,
    }),
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000004",
      clusterSlug: "sandbox",
      gatewayEnabled: false,
      gatewayDeployed: false,
      status: "registered",
    }),
  ],
};

/** A gateway Zentinelle has not heard from, and one that never reported. */
export const STALE_CONNECTION: ZentinelleConnection = {
  ...HEALTHY_CONNECTION,
  clusters: [
    cluster({}),
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000002",
      clusterSlug: "prod-eu-west-1",
      zentinelleClusterId: "0199a1b2-0000-7000-8000-000000000002",
      lastSeenAt: ago(42),
    }),
    cluster({
      clusterId: "0199a1b2-0000-7000-8000-000000000003",
      clusterSlug: "staging",
      zentinelleClusterId: "0199a1b2-0000-7000-8000-000000000003",
      gatewayVersion: "",
      lastSeenAt: null,
    }),
  ],
};

/** A Zentinelle released before the health read: nothing to report. */
export const OLDER_ZENTINELLE_CONNECTION: ZentinelleConnection = {
  ...HEALTHY_CONNECTION,
  serverVersion: null,
  clusters: HEALTHY_CONNECTION.clusters.map((c) => ({
    ...c,
    gatewayVersion: null,
    lastSeenAt: null,
  })),
};

export const HEALTH_COUNTS: ZentinelleHealthCounts = {
  available: true,
  windowHours: 24,
  blocks: 37,
  openApprovals: 2,
};

export const HEALTH_NOT_AVAILABLE: ZentinelleHealthCounts = {
  available: false,
  windowHours: null,
  blocks: null,
  openApprovals: null,
};

export const REVOKED_CONNECTION: ZentinelleConnection = {
  ...HEALTHY_CONNECTION,
  status: "revoked",
  lastError: "Zentinelle answered 401 for the install credential",
};

export const USAGE_ROWS: ZentinelleUsageRow[] = [
  {
    agentSlug: "triage-bot",
    provider: "anthropic",
    model: "claude-sonnet-4-5",
    requests: 1842,
    inputTokens: 3_120_400,
    outputTokens: 412_900,
  },
  {
    agentSlug: "release-notes",
    provider: "openai",
    model: "gpt-5-mini",
    requests: 214,
    inputTokens: 402_000,
    outputTokens: 96_500,
  },
  {
    agentSlug: "a-very-long-agent-slug-that-belongs-to-the-platform-reliability-team",
    provider: "bedrock",
    model: "anthropic.claude-haiku-4-5-20251001-v1:0",
    requests: 9,
    inputTokens: 18_000,
    outputTokens: 2_100,
  },
];

export const POLICIES: ZentinellePolicy[] = [
  {
    id: "pol_1",
    name: "Block secrets in prompts",
    policyType: "content_scan",
    enforcement: "block",
    scope: "tenant",
    priority: 10,
    description: "Rejects requests whose prompt carries an API key, private key or password.",
  },
  {
    id: "pol_2",
    name: "Human oversight",
    policyType: "human_oversight",
    enforcement: "warn",
    scope: "tenant",
    priority: 50,
    description: "",
  },
  {
    id: "pol_3",
    name: "Approved models only",
    policyType: "model_allowlist",
    enforcement: "block",
    scope: "deployment:triage-bot",
    priority: 20,
    description: "triage-bot may call claude-sonnet-4-5 and nothing else.",
  },
];

export const AUDIT_EVENTS: ZentinelleAuditEvent[] = [
  {
    id: "ev_1",
    occurredAt: "2026-10-16T08:01:00Z",
    action: "policy.block",
    resourceType: "request",
    resourceId: "req_91",
    resourceName: "Block secrets in prompts",
    agentSlug: "triage-bot",
    agentId: "agt_1",
    actor: "gateway/prod-us-east-1",
  },
  {
    id: "ev_2",
    occurredAt: "2026-10-16T08:05:00Z",
    action: "agent.run.completed",
    resourceType: "run",
    resourceId: "run_42",
    resourceName: "",
    agentSlug: "release-notes",
    agentId: "agt_2",
    actor: "",
  },
];

const scopeOptions = {
  projects: [
    { id: "0199a1b2-0000-7000-8000-0000000000p1", name: "Payments" },
    { id: "0199a1b2-0000-7000-8000-0000000000p2", name: "Support" },
  ],
  teams: [{ id: "0199a1b2-0000-7000-8000-0000000000t1", name: "Platform" }],
};

const filter = {
  scope: ALL_SCOPE,
  onScopeChange: () => {},
  options: scopeOptions,
  range: "7d" as const,
  onRangeChange: () => {},
};

export const CONNECTED: ZentinelleScreenProps = {
  loading: false,
  error: null,
  access: FULL_ACCESS,
  connected: true,
  connection: HEALTHY_CONNECTION,
  tab: "overview",
  tabHref: (tab) => `?tab=${tab}`,
  connect: { connecting: false, onConnect: ok },
  manage: { busy: false, onSetGatewayUrl: ok, onDisconnect: ok },
  health: {
    usage: {
      data: { day: usageTotals(USAGE_ROWS.slice(0, 1)), month: usageTotals(USAGE_ROWS) },
      loading: false,
      error: null,
    },
    counts: { data: HEALTH_COUNTS, loading: false, error: null },
  },
  clusters: {
    busyClusterId: null,
    choices: {
      items: [
        {
          id: "0199a1b2-0000-7000-8000-000000000001",
          slug: "prod-us-east-1",
          name: "Prod US",
          shared: false,
        },
        {
          id: "0199a1b2-0000-7000-8000-000000000009",
          slug: "gpu-pool",
          name: "GPU pool",
          shared: false,
        },
        {
          id: "0199a1b2-0000-7000-8000-000000000021",
          slug: "platform-eu-west-1",
          name: "Platform EU",
          shared: true,
        },
      ],
      loading: false,
      search: "",
    },
    onSearch: () => {},
    onRegister: ok,
    onSetEnabled: ok,
    onRotate: ok,
    onUnregister: ok,
    shared: { operator: false, connection: null, onRegister: ok, onUnregister: ok },
  },
  usage: { filter, usage: { data: USAGE_ROWS, loading: false, error: null } },
  policies: { filter, policies: { data: POLICIES, loading: false, error: null } },
  audit: {
    filter,
    feed: { items: AUDIT_EVENTS, loading: false, error: null, hasMore: true, loadingMore: false },
    exporting: false,
    onExport: () => {},
  },
};

export const NOT_CONNECTED: ZentinelleScreenProps = {
  ...CONNECTED,
  connected: false,
  connection: null,
};

export const UPSTREAM_ERROR =
  "Zentinelle could not be reached: connection refused (zentinelle.example.com:443)";
