import { fakeController } from "@/components/data-table/fixtures";
import type {
  AstroliftAgentBox,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";

import { type AgentRow, joinAgents } from "./agents-list";
import type { AgentsListScreenProps } from "./AgentsListScreen";
import type { BoxesTabViewProps } from "./BoxesTabView";
import type { ManagedModelSectionViewProps } from "./ManagedModelSectionView";
import type { VncSessionSectionViewProps } from "./VncSessionSectionView";

/** Hand-typed fixtures for the Agents list and the parts it handed on (group agents-list). */

const noop = () => {};
const resolvedTrue = async () => true;
const resolved = async () => {};

/** The JSON scalar is typed Record<string, unknown>; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const LONG_NAME =
  "An extremely long agent name that keeps going to check wrapping in the registry table cell";

// ---------------------------------------------------------------------------
// Fleet rows
// ---------------------------------------------------------------------------

export function agent(
  slug: string,
  patch: Partial<AstroliftAgentListItem> = {}
): AstroliftAgentListItem {
  return {
    id: `wl-${slug}`,
    name: slug,
    slug,
    appSlug: "support",
    projectSlug: "platform",
    sourceRepo: "calliopeai/support-agents",
    sourceUrl: "https://github.com/calliopeai/support-agents",
    runFamily: "task",
    runMode: "schedule",
    runPaused: false,
    runCronExpression: "0 * * * *",
    lastRunStatus: "completed",
    lastRunAt: "2026-09-28T09:00:00Z",
    runningCount: 0,
    ...patch,
  };
}

export const FLEET: AstroliftAgentListItem[] = [
  agent("triage-bot", { name: "Triage bot", runningCount: 2, lastRunStatus: "running" }),
  agent("nightly-report", { name: "Nightly report", lastRunStatus: "failed", appSlug: "reports" }),
  agent("paused-sync", { name: "Paused sync", runPaused: true, runMode: "loop" }),
  agent("docs-service", {
    name: "Docs service",
    projectSlug: "docs",
    appSlug: "docs",
    runFamily: "service",
    runMode: "service",
    sourceUrl: "",
    lastRunStatus: null,
    lastRunAt: null,
  }),
  agent("no-repo", { name: "No repo", sourceRepo: "", sourceUrl: "", runMode: "custom_mode" }),
];

function live(
  workloadId: string,
  patch: Partial<AstroliftAgentLiveStatus> = {}
): AstroliftAgentLiveStatus {
  return {
    workloadId,
    workloadSlug: workloadId.replace(/^wl-/, ""),
    appSlug: "support",
    runFamily: "task",
    runMode: "schedule",
    isPaused: false,
    isIdle: true,
    runningCount: 0,
    lastRunStatus: null,
    lastRunAt: null,
    nextScheduledAt: null,
    ...patch,
  };
}

export const LIVE: AstroliftAgentLiveStatus[] = [
  live("wl-triage-bot", { runningCount: 2, isIdle: false }),
  live("wl-docs-service", { nextScheduledAt: "2026-09-28T23:00:00Z" }),
];

/** Spec slug is the agent slug: triage-bot runs claude on the managed model. */
const AGENT_SPECS = [
  { slug: "triage-bot", runtime: "claude", managedModel: true },
  { slug: "nightly-report", runtime: "codex", managedModel: false },
  { slug: "paused-sync", runtime: "claude", managedModel: false },
];

const ENVIRONMENTS = [
  { registeredAppSlug: "support", clusterSlug: "conflict-astrolift" },
  { registeredAppSlug: "support", clusterSlug: "eks-us-west-2" },
  { registeredAppSlug: "reports", clusterSlug: "gke-eu-west-4" },
];

/** The fleet joined the way the hook joins it; the viewer holds a role on `support`. */
export const AGENT_ROWS: AgentRow[] = joinAgents(FLEET, {
  live: LIVE,
  specs: AGENT_SPECS,
  environments: ENVIRONMENTS,
  myAppSlugs: new Set(["support"]),
});

export const LONG_AGENT_ROW: AgentRow = joinAgents(
  [
    agent("a-very-long-agent-slug-that-keeps-going-and-going-for-wrapping-0123456789abcdef", {
      name: LONG_NAME,
      projectSlug: "a-very-long-project-slug-for-the-coordinates-line",
      appSlug:
        "arn:aws:ecs:us-west-2:123456789012:service/astrolift-agents/a-very-long-app-slug-for-the-coordinates-line-and-then-some-more-characters-to-pass-two-hundred",
      sourceRepo: "calliopeai/an-agent-repository-with-an-unreasonably-long-name-for-layout",
      sourceUrl:
        "https://github.com/calliopeai/an-agent-repository-with-an-unreasonably-long-name-for-layout/tree/main/agents/triage",
      lastRunStatus: "timed_out",
    }),
  ],
  {
    live: [],
    specs: [
      {
        slug: "a-very-long-agent-slug-that-keeps-going-and-going-for-wrapping-0123456789abcdef",
        runtime: "a-custom-runtime-with-a-long-name",
        managedModel: false,
      },
    ],
    environments: [],
    myAppSlugs: new Set(),
  }
)[0];

/** 60 agents, for numbered pages. */
export const MANY_AGENT_ROWS: AgentRow[] = Array.from({ length: 60 }, (_, i) => ({
  ...AGENT_ROWS[i % AGENT_ROWS.length],
  id: `wl-agent-${i}`,
  slug: `agent-${String(i).padStart(2, "0")}`,
  name: `Agent ${String(i).padStart(2, "0")}`,
}));

/** Everything the screen takes except `list`, which each story builds. */
export function listProps(
  patch: Partial<Omit<AgentsListScreenProps, "list">> = {}
): Omit<AgentsListScreenProps, "list"> {
  return {
    rows: AGENT_ROWS,
    totalCount: AGENT_ROWS.length,
    loading: false,
    stale: false,
    error: null,
    onRetry: noop,
    canCreate: true,
    canRun: true,
    dispatching: false,
    onRun: resolvedTrue,
    ...patch,
  };
}

// ---------------------------------------------------------------------------
// Boxes
// ---------------------------------------------------------------------------

export function box(slug: string, patch: Partial<AstroliftAgentBox> = {}): AstroliftAgentBox {
  return {
    id: `box-${slug}`,
    name: "Claude Dev box",
    slug,
    status: "running",
    agentSlug: "",
    environmentSpecSlug: "claude-dev",
    image: "agent-claude:1",
    idleTimeoutSeconds: 3600,
    sessionName: "astrolift",
    attachCommand: ["tmux", "new-session", "-A", "-s", "astrolift"],
    namespace: "astrolift-agents-acme",
    podName: `${slug}-0`,
    ownerEmail: "dev@example.com",
    lastError: "",
    startedAt: "2026-09-28T08:00:00Z",
    endedAt: null,
    lastAttachedAt: null,
    createdAt: "2026-09-28T08:00:00Z",
    ...patch,
  };
}

export const BOXES: AstroliftAgentBox[] = [
  box("box-claude-dev-abcd1234"),
  box("box-codex-5678efgh", {
    name: "Codex box",
    status: "provisioning",
    environmentSpecSlug: "codex",
    idleTimeoutSeconds: 0,
    startedAt: null,
  }),
  box("box-failed-0000", {
    name: "Failed box",
    status: "failed",
    idleTimeoutSeconds: 86400,
    lastError: "ImagePullBackOff: agent-claude:1 not found in registry",
  }),
  box("box-expired-9999", { name: "Expired box", status: "expired", idleTimeoutSeconds: 900 }),
];

export const LONG_BOXES: AstroliftAgentBox[] = [
  box("box-an-unreasonably-long-box-slug-that-keeps-going-for-layout-checks-0123456789", {
    name: LONG_NAME,
    lastError:
      "A very long error message from the provisioner that should clamp to two lines in the status cell rather than pushing the whole row taller than every other row in the table.",
  }),
];

export const SPECS: AstroliftAgentEnvironmentSpec[] = [
  {
    id: "s1",
    slug: "claude-dev",
    name: "Claude Dev",
    runtime: "claude",
    imageTag: "1",
    agentType: "claude",
    vncEnabled: false,
    secretRefs: json({ ANTHROPIC_API_KEY: "agents/org-1/anthropic" }),
    managedModel: false,
  },
  {
    id: "s2",
    slug: "codex",
    name: "Codex",
    runtime: "",
    imageTag: "1",
    agentType: "codex",
    vncEnabled: false,
    secretRefs: json([]),
    managedModel: false,
  },
];

export function boxesProps(
  rows: AstroliftAgentBox[] = BOXES,
  patch: Partial<BoxesTabViewProps> = {}
): BoxesTabViewProps {
  return {
    controller: fakeController<AstroliftAgentBox>({
      rows,
      totalCount: rows.length,
      state: rows.length ? "ready" : "empty",
      searchEnabled: false,
      sortEnabled: false,
    }),
    specs: SPECS,
    starting: false,
    includeEnded: false,
    toggleIncludeEnded: noop,
    startBox: resolvedTrue,
    destroyBox: resolved,
    copyAttach: noop,
    ...patch,
  };
}

// ---------------------------------------------------------------------------
// Spec toggles
// ---------------------------------------------------------------------------

export function vncProps(
  patch: Partial<VncSessionSectionViewProps> = {}
): VncSessionSectionViewProps {
  return { vncOn: false, vncBusy: false, onToggleVnc: resolved, ...patch };
}

export function managedModelProps(
  patch: Partial<ManagedModelSectionViewProps> = {}
): ManagedModelSectionViewProps {
  return { managedOn: false, managedBusy: false, onToggleManagedModel: resolved, ...patch };
}
