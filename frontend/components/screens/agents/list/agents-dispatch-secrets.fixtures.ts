import type {
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentSecretBundle,
  AstroliftAgentSecretBundleAttachment,
  AstroliftAgentSecretStatus,
} from "@/graphql/agents/agents.types";

import type { AgentSecretBundlesViewProps } from "./AgentSecretBundles";
import type { AgentSecretsViewProps } from "./AgentSecrets";
import type { DispatchTabViewProps } from "./DispatchTab";

/** Hand-typed fixtures for the Dispatch tab and the agent secrets dialog. */

const yes = async () => true;
const noop = async () => {};

/** The JSON scalar is typed as an object but carries any JSON (here a ref list). */
const json = (value: unknown) => value as Record<string, unknown>;

export const LONG =
  "platform-team-shared-production-research-agent-us-west-2-with-a-deliberately-long-name-that-keeps-going";

export const AGENT: AstroliftAgentListItem = {
  id: "agent-1",
  name: "Release notes writer",
  slug: "release-notes-writer",
  appSlug: "release-notes",
  projectSlug: "platform",
  sourceRepo: "calliopeai/agents",
  sourceUrl: "https://github.com/calliopeai/agents",
  runFamily: "job",
  runMode: "schedule",
  runPaused: false,
  runCronExpression: "0 9 * * 1",
  lastRunStatus: "succeeded",
  lastRunAt: "2026-09-27T09:00:00Z",
  runningCount: 0,
};

export const AGENTS: AstroliftAgentListItem[] = [
  AGENT,
  {
    ...AGENT,
    id: "agent-2",
    name: "Triage bot",
    slug: "triage-bot",
    appSlug: "triage",
    runMode: "once",
    runCronExpression: "",
    runPaused: true,
    lastRunStatus: "failed_preflight",
  },
  {
    ...AGENT,
    id: "agent-3",
    name: "Docs indexer",
    slug: "docs-indexer",
    appSlug: "docs",
    runFamily: "service",
    runMode: "",
    runCronExpression: "",
    lastRunStatus: null,
    lastRunAt: null,
  },
];

export const LONG_AGENT: AstroliftAgentListItem = {
  ...AGENT,
  id: "agent-long",
  name: LONG,
  slug: LONG,
  appSlug: LONG,
  projectSlug: LONG,
  runCronExpression: "*/5 0-23 1-31 1-12 MON-FRI",
  lastRunStatus: "cancelled_by_operator_after_timeout_exceeded",
};

export const ENV_SPECS: AstroliftAgentEnvironmentSpec[] = [
  {
    id: "spec-1",
    slug: "claude-code-default",
    name: "Claude Code default",
    runtime: "claude-code",
    imageTag: "ghcr.io/calliopeai/agent:0.1.37",
    agentType: "claude",
    vncEnabled: true,
    managedModel: false,
    secretRefs: json([
      { uri: "agents/org-1/anthropic", env_var: "ANTHROPIC_API_KEY" },
      { uri: "agents/org-1/github", env_var: "GITHUB_TOKEN" },
    ]),
  },
  {
    id: "spec-2",
    slug: "bedrock",
    name: "Bedrock (managed model)",
    runtime: "",
    imageTag: "",
    agentType: "claude",
    vncEnabled: false,
    managedModel: true,
    secretRefs: json([]),
  },
];

export const DISPATCH: DispatchTabViewProps = {
  agents: AGENTS,
  envSpecs: ENV_SPECS,
  fleetLoading: false,
  dispatching: false,
  onDispatch: async () => ({ id: "task-42" }),
};

// ---------------------------------------------------------------------------
// Secrets dialog
// ---------------------------------------------------------------------------

export const SECRET_ROWS: AstroliftAgentSecretStatus[] = [
  {
    envVar: "ANTHROPIC_API_KEY",
    uri: "agents/org-1/anthropic",
    exists: true,
    error: null,
    provider: "aws-secrets-manager",
    canReveal: true,
    readLimitation: null,
  },
  {
    envVar: "GITHUB_TOKEN",
    uri: "agents/org-1/github",
    exists: false,
    error: null,
    provider: "aws-secrets-manager",
    canReveal: true,
    readLimitation: null,
  },
  {
    envVar: "SLACK_WEBHOOK",
    uri: "agents/org-1/slack",
    exists: true,
    error: null,
    provider: "vault",
    canReveal: false,
    readLimitation: "Write-only provider: values cannot be read back",
  },
];

export const SECRET_ROW_ERROR: AstroliftAgentSecretStatus = {
  envVar: "DATABASE_URL",
  uri: "agents/org-1/database",
  exists: false,
  error: "AccessDeniedException: not authorized to perform secretsmanager:DescribeSecret",
  provider: "aws-secrets-manager",
  canReveal: false,
  readLimitation: null,
};

export const SECRETS: AgentSecretsViewProps = {
  rows: SECRET_ROWS,
  loading: false,
  refNamespace: "agents/org-1",
  reveals: {},
  busyVar: "",
  clearReveals: () => {},
  onSave: yes,
  onDeleteValue: noop,
  onUpsertRef: yes,
  onRemoveRef: noop,
  onReveal: noop,
  envSpecName: "Claude Code default",
  open: true,
};

export const LONG_SECRET_ROW: AstroliftAgentSecretStatus = {
  envVar: `${LONG.toUpperCase().replace(/-/g, "_")}_TOKEN`,
  uri: `agents/org-1/${LONG}/${LONG}`,
  exists: true,
  error: null,
  provider: "aws-secrets-manager",
  canReveal: false,
  readLimitation: `Cross-account role cannot read ${LONG}`,
};

// ---------------------------------------------------------------------------
// Reusable secret bundles
// ---------------------------------------------------------------------------

export const BUNDLES_LIST: AstroliftAgentSecretBundle[] = [
  {
    id: "bundle-1",
    slug: "github-app",
    name: "GitHub app",
    provider: "aws-secrets-manager",
    backendRef: "arn:aws:secretsmanager:us-west-2:123456789012:secret:agents/org-1/github-app",
    keyNames: ["GITHUB_APP_ID", "GITHUB_PRIVATE_KEY"],
    canReveal: true,
    readLimitation: null,
    createdAt: "2026-09-01T10:00:00Z",
    updatedAt: "2026-09-20T10:00:00Z",
  },
  {
    id: "bundle-2",
    slug: "observability",
    name: "Observability",
    provider: "vault",
    backendRef: "secret/data/agents/org-1/observability",
    keyNames: ["DD_API_KEY"],
    canReveal: false,
    readLimitation: "Write-only provider: values cannot be read back",
    createdAt: "2026-09-02T10:00:00Z",
    updatedAt: "2026-09-02T10:00:00Z",
  },
];

export const ATTACHMENT: AstroliftAgentSecretBundleAttachment = {
  id: "attach-1",
  bundleId: "bundle-1",
  bundleName: "GitHub app",
  bundleSlug: "github-app",
  environment: "default",
  keyNames: ["GITHUB_APP_ID", "GITHUB_PRIVATE_KEY"],
  position: 0,
  prefix: "GH_",
};

export const BUNDLES: AgentSecretBundlesViewProps = {
  bundles: BUNDLES_LIST,
  defaultAttachments: [ATTACHMENT],
  loading: false,
  busy: "",
  reveals: {},
  onCreate: yes,
  onUpdate: yes,
  onDeleteBundle: noop,
  onAttach: yes,
  onDetach: noop,
  onSetKey: yes,
  onDeleteKey: noop,
  onRevealKey: noop,
};

export const LONG_BUNDLE: AstroliftAgentSecretBundle = {
  ...BUNDLES_LIST[0],
  id: "bundle-long",
  name: LONG,
  slug: LONG,
  backendRef: `arn:aws:secretsmanager:us-west-2:123456789012:secret:${LONG}/${LONG}`,
  keyNames: [`${LONG.toUpperCase().replace(/-/g, "_")}_KEY`],
  canReveal: false,
  readLimitation: `Cross-account role cannot read ${LONG}`,
};
