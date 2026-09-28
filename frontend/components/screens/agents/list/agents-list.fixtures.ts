import { fakeController } from "@/components/data-table/fixtures";
import type {
  AstroliftAgentBox,
  AstroliftAgentEnvironmentSpec,
  AstroliftAgentListItem,
  AstroliftAgentLiveStatus,
} from "@/graphql/agents/agents.types";

import type { ActiveTasksPanelProps } from "./ActiveTasksPanel";
import type { AgentRegistryPanelProps } from "./AgentRegistryPanel";
import { AGENT_TABS, ZENTINELLE_TABS } from "./agents-list-tabs";
import type { AgentsScreenProps } from "./AgentsScreen";
import type { BoxesTabViewProps } from "./BoxesTabView";
import type { ManagedModelSectionViewProps } from "./ManagedModelSectionView";
import type { TaskHistoryPanelProps } from "./TaskHistoryPanel";
import type { AgentTask } from "./use-agent-tasks";
import type { VncSessionSectionViewProps } from "./VncSessionSectionView";

/** Hand-typed fixtures for the Agents fleet page (group agents-list). */

const noop = () => {};
const resolvedTrue = async () => true;
const resolved = async () => {};

/** The JSON scalar is typed Record<string, unknown>; real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const LONG_ID = "task-7f3c2a91-0b4e-4c55-9d2e-" + "a".repeat(64);
const LONG_NAME =
  "An extremely long agent name that keeps going to check wrapping in the registry table cell";

// ---------------------------------------------------------------------------
// Screen
// ---------------------------------------------------------------------------

export function screenProps(patch: Partial<AgentsScreenProps> = {}): AgentsScreenProps {
  return {
    visibleTabs: AGENT_TABS.filter((t) => !ZENTINELLE_TABS.has(t)),
    tab: "active",
    onTabChange: noop,
    orgId: "org-1",
    panels: {},
    ...patch,
  };
}

// ---------------------------------------------------------------------------
// Tasks (Active / History)
// ---------------------------------------------------------------------------

export function task(id: string, patch: Partial<AgentTask> = {}): AgentTask {
  return {
    id,
    status: "running",
    callbackUrl: "",
    result: null,
    createdAt: "2026-09-28T09:58:00Z",
    startedAt: "2026-09-28T09:58:04Z",
    finishedAt: null,
    vncEnabled: false,
    vncUrl: "",
    ...patch,
  };
}

export const ACTIVE_TASKS: AgentTask[] = [
  task("7f3c2a91", { vncEnabled: true, vncUrl: "/vnc/7f3c2a91" }),
  task("0b4e4c55"),
  task("9d2ea1f0", { vncEnabled: true, vncUrl: "" }),
];

export const HISTORY_TASKS: AgentTask[] = [
  task("1a2b3c4d", { status: "completed", finishedAt: "2026-09-28T09:40:00Z" }),
  task("5e6f7a8b", { status: "failed", finishedAt: "2026-09-28T08:12:00Z" }),
  task("9c0d1e2f", { status: "timed_out", finishedAt: "2026-09-27T22:01:00Z" }),
  task("3a4b5c6d", { status: "cancelled", finishedAt: null }),
];

export const LONG_TASKS: AgentTask[] = [
  task(LONG_ID, { vncEnabled: true, vncUrl: "/vnc/long", status: "running" }),
];

export const LONG_HISTORY_TASKS: AgentTask[] = [
  task(LONG_ID, { status: "completed", finishedAt: "2026-09-28T09:40:00Z" }),
];

export function activeProps(patch: Partial<ActiveTasksPanelProps> = {}): ActiveTasksPanelProps {
  return { tasks: ACTIVE_TASKS, loading: false, ...patch };
}

export function historyProps(patch: Partial<TaskHistoryPanelProps> = {}): TaskHistoryPanelProps {
  return { tasks: HISTORY_TASKS, loading: false, ...patch };
}

// ---------------------------------------------------------------------------
// Registry
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

export const AGENTS: AstroliftAgentListItem[] = [
  agent("triage-bot", { name: "Triage bot", runningCount: 2, lastRunStatus: "running" }),
  agent("nightly-report", { name: "Nightly report", lastRunStatus: "failed" }),
  agent("paused-sync", { name: "Paused sync", runPaused: true, runMode: "loop" }),
  agent("docs-service", {
    name: "Docs service",
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

export const LIVE_BY_WORKLOAD = new Map<string, AstroliftAgentLiveStatus>([
  ["wl-triage-bot", live("wl-triage-bot", { runningCount: 2, isIdle: false })],
  ["wl-nightly-report", live("wl-nightly-report", { nextScheduledAt: "2026-09-28T23:00:00Z" })],
]);

export const LONG_AGENTS: AstroliftAgentListItem[] = [
  agent("a-very-long-agent-slug-that-keeps-going-and-going-for-wrapping", {
    name: LONG_NAME,
    projectSlug: "a-very-long-project-slug-for-the-coordinates-line",
    appSlug: "a-very-long-app-slug-for-the-coordinates-line",
    sourceRepo: "calliopeai/an-agent-repository-with-an-unreasonably-long-name-for-layout",
  }),
];

export const PROJECTS = [
  { id: "p1", slug: "platform", name: "Platform" },
  { id: "p2", slug: "support", name: "Support" },
];

export function registryProps(
  patch: Partial<AgentRegistryPanelProps> = {}
): AgentRegistryPanelProps {
  return {
    canCreateAgent: true,
    projectSlug: "",
    fleet: true,
    setProjectScope: noop,
    projects: PROJECTS,
    agents: AGENTS,
    loading: false,
    liveByWorkloadId: LIVE_BY_WORKLOAD,
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
