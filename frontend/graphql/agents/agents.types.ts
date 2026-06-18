import type {
  AstroliftAgentListItem as GeneratedAgentListItem,
  AstroliftAgentLiveStatus as GeneratedAgentLiveStatus,
  AstroliftAgentTask as GeneratedAgentTask,
  AstroliftBrief as GeneratedBrief,
  AstroliftDispatcherInstance as GeneratedDispatcherInstance,
  AstroliftSkill as GeneratedSkill,
  AstroliftToolDef as GeneratedToolDef,
} from "@/graphql/__generated__/schema";

export type AstroliftSkill = Pick<
  GeneratedSkill,
  "id" | "name" | "slug" | "description" | "content" | "skillVersion" | "isGlobal" | "isActive" | "createdAt" | "updatedAt"
>;

export type AstroliftToolDef = Pick<
  GeneratedToolDef,
  "id" | "name" | "slug" | "description" | "adapter" | "inputSchema" | "outputSchema" | "handlerRef" | "createdAt"
>;

export type AstroliftBrief = Pick<
  GeneratedBrief,
  "id" | "contentHash" | "storageKey" | "config" | "createdAt"
>;

export type AstroliftAgentTask = Pick<
  GeneratedAgentTask,
  "id" | "status" | "callbackUrl" | "result" | "createdAt" | "startedAt" | "finishedAt"
>;

// Registry list row (PR-7). The full row as returned by `agentWorkloads` /
// `agentFleet` — every field is queried, so the facade is the whole type.
export type AstroliftAgentListItem = Pick<
  GeneratedAgentListItem,
  | "id"
  | "name"
  | "slug"
  | "appSlug"
  | "projectSlug"
  | "sourceRepo"
  | "sourceUrl"
  | "runFamily"
  | "runMode"
  | "runPaused"
  | "runCronExpression"
  | "lastRunStatus"
  | "lastRunAt"
  | "runningCount"
>;

// Volatile live-status companion (PR-7), merged into list rows by workloadId.
export type AstroliftAgentLiveStatus = Pick<
  GeneratedAgentLiveStatus,
  | "workloadId"
  | "workloadSlug"
  | "appSlug"
  | "runFamily"
  | "runMode"
  | "isPaused"
  | "isIdle"
  | "runningCount"
  | "lastRunStatus"
  | "lastRunAt"
  | "nextScheduledAt"
>;

export type AstroliftDispatcherInstance = Pick<
  GeneratedDispatcherInstance,
  "id" | "serviceUrl" | "capabilities" | "lastHeartbeat" | "registeredAt"
>;
