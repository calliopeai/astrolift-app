import type { AgentTask } from "./use-agent-observe";

/** Hand-typed fixtures for the agent Observe and Secure tabs. */

export const LONG =
  "platform-team-shared-production-research-agent-with-a-deliberately-long-name-that-keeps-going-and-going";

export const RUNNING_TASK: AgentTask = {
  id: "3f9c2a1e-7b4d-4e8a-9c61-2d5f0b8e4a17",
  status: "running",
  createdAt: "2026-09-28T14:02:00Z",
  startedAt: "2026-09-28T14:02:09Z",
  finishedAt: null,
  failureMessage: null,
};

export const OLDER_TASK: AgentTask = {
  id: "8a1d0c55-2e3f-4b7a-8d90-6c4e1f2a3b5d",
  status: "succeeded",
  createdAt: "2026-09-27T09:10:00Z",
  startedAt: "2026-09-27T09:10:07Z",
  finishedAt: "2026-09-27T09:24:31Z",
  failureMessage: null,
};

export const FAILED_TASK: AgentTask = {
  id: "c7e4b2a9-1f3d-4c8e-a5b6-9d0e2f4a6c81",
  status: "failed",
  createdAt: "2026-09-28T15:30:00Z",
  startedAt: null,
  finishedAt: "2026-09-28T15:30:04Z",
  failureMessage:
    'spawn failed: pods "agent-research-7f9c" is forbidden: exceeded quota: agents-quota, requested: limits.memory=4Gi, used: limits.memory=14Gi, limited: limits.memory=16Gi',
};

export const LOG_LINES = [
  "2026-09-28T14:02:09Z INFO  agent starting (model=claude-sonnet, tools=4)",
  "2026-09-28T14:02:10Z INFO  connected to gateway zentinelle.conflict.softinfra.net",
  "2026-09-28T14:02:12Z INFO  task: summarize open incidents for the platform team",
  "2026-09-28T14:02:15Z DEBUG tool call: incidents.list(status=open, limit=25)",
  "2026-09-28T14:02:16Z DEBUG tool result: 7 incidents",
  "2026-09-28T14:02:21Z INFO  drafting summary",
];

export const LONG_LOG_LINES = [
  ...LOG_LINES,
  `2026-09-28T14:02:22Z WARN  ${LONG.repeat(4)}`,
  ...Array.from(
    { length: 40 },
    (_, i) => `2026-09-28T14:03:${String(i).padStart(2, "0")}Z DEBUG step ${i + 1}`
  ),
];
