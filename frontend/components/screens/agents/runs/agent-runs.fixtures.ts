import type { AstroliftAgentInteraction, AstroliftAgentTask } from "@/graphql/agents/agents.types";

import type { AgentInteractionMapProps } from "./AgentInteractionMap";
import type { AgentRunDetailProps } from "./AgentRunDetail";
import type { AgentVncPopoutProps } from "./AgentVncPopout";

/** The JSON scalar is typed as an object, but real values are any JSON. */
const json = (value: unknown) => value as Record<string, unknown>;

const ago = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();

export const TASK_ID = "7f3c2a10-4d5e-4f60-9a1b-2c3d4e5f6a7b";

export const RUNNING_TASK: AstroliftAgentTask = {
  id: TASK_ID,
  agentSlug: "bdr-outreach",
  agentName: "BDR outreach",
  projectSlug: "sales",
  status: "running",
  callbackUrl: "https://hooks.example.com/agents/bdr-outreach/callback",
  result: null,
  failureMessage: null,
  createdAt: ago(600),
  startedAt: ago(540),
  finishedAt: null,
  vncEnabled: true,
  vncUrl: `/agents/vnc/${TASK_ID}`,
  snapshotUrl: null,
  podName: "agent-bdr-outreach-7f3c2a10-x2k9p",
  namespace: "agents-sales",
};

export const COMPLETED_TASK: AstroliftAgentTask = {
  ...RUNNING_TASK,
  status: "completed",
  finishedAt: ago(60),
  vncUrl: "",
  result: json({ drafted: 3, routedTo: "approval-gate", accounts: ["acme", "globex", "initech"] }),
};

export const FAILED_TASK: AstroliftAgentTask = {
  ...RUNNING_TASK,
  status: "failed",
  startedAt: null,
  finishedAt: ago(580),
  vncEnabled: false,
  vncUrl: "",
  podName: "",
  failureMessage:
    'spawn failed: secret "agents/org-1/bdr-outreach/crm-token" not found in the agent secret store',
};

const LONG = "a-very-long-identifier-that-keeps-going-to-show-how-the-layout-wraps".repeat(3);

export const LONG_TASK: AstroliftAgentTask = {
  ...RUNNING_TASK,
  podName: `agent-${LONG}`,
  namespace: `agents-${LONG}`,
  callbackUrl: `https://hooks.example.com/agents/${LONG}/callback?token=${LONG}`,
  result: json({ note: LONG, nested: { list: [LONG, LONG] } }),
  failureMessage: `runtime error: ${LONG} ${LONG}`,
};

export const LOG_LINES = [
  "2026-09-28T12:00:01Z INFO  agent starting: bdr-outreach v0.1.37",
  "2026-09-28T12:00:04Z INFO  fetching 25 accounts from the CRM",
  "2026-09-28T12:00:12Z INFO  drafted 3 messages; routing to the approval gate",
];

const interaction = (
  id: string,
  kind: string,
  name: string,
  status: string,
  secondsAgo: number
): AstroliftAgentInteraction => ({ id, kind, name, status, occurredAt: ago(secondsAgo) });

export const INTERACTIONS: AstroliftAgentInteraction[] = [
  interaction("i1", "control_api", "POST /agents/tasks/heartbeat", "ok", 5),
  interaction("i2", "control_api", "POST /agents/tasks/heartbeat", "ok", 65),
  interaction("i3", "tool_call", "crm.search_accounts", "ok", 120),
  interaction("i4", "tool_call", "email.draft", "error", 90),
  interaction("i5", "gate", "outreach-approval", "pending", 30),
];

export const LONG_INTERACTIONS: AstroliftAgentInteraction[] = [
  interaction("l1", "tool_call", `tool.${LONG}`, "ok", 10),
  interaction("l2", "signal", `signal.${LONG}`, "ok", 400),
];

export const INTERACTION_MAP: AgentInteractionMapProps = {
  taskStatus: "running",
  isTerminal: false,
  interactions: INTERACTIONS,
  loading: false,
  error: null,
};

const noop = () => {};

export const RUN_DETAIL: AgentRunDetailProps = {
  taskId: TASK_ID,
  task: RUNNING_TASK,
  loading: false,
  error: null,
  onRetry: noop,
  terminal: false,
  now: Date.now(),
  logs: LOG_LINES,
  logsLoading: false,
  logsError: null,
  onRetryLogs: noop,
  onDownloadLogs: noop,
  onHardStop: async () => {},
  interactions: INTERACTION_MAP,
};

/** Enough tool calls that the Timeline folds the oldest into one line. */
export const MANY_INTERACTIONS: AstroliftAgentInteraction[] = Array.from({ length: 60 }, (_, i) =>
  interaction(
    `m${i}`,
    "tool_call",
    `crm.lookup_account_${i}`,
    i % 7 === 0 ? "error" : "ok",
    600 - i * 9
  )
);

export const VNC_POPOUT: AgentVncPopoutProps = {
  onRetry: () => {},
  taskId: TASK_ID,
  task: {
    id: TASK_ID,
    status: "running",
    startedAt: ago(540),
    vncEnabled: true,
    vncUrl: `/agents/vnc/${TASK_ID}`,
    snapshotUrl: null,
  },
  loading: false,
  error: null,
};
