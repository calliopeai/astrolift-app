import type {
  AstroliftAgentListItem as GeneratedAgentListItem,
  AstroliftAgentLiveStatus as GeneratedAgentLiveStatus,
  AstroliftAgentTask as GeneratedAgentTask,
  AstroliftBrief as GeneratedBrief,
  AstroliftDiscoveredAgentManifest as GeneratedDiscoveredAgentManifest,
  AstroliftDispatcherInstance as GeneratedDispatcherInstance,
  AstroliftRegisterAgentRepoResult as GeneratedRegisterAgentRepoResult,
  AstroliftRegisteredAgent as GeneratedRegisteredAgent,
  AstroliftScanAgentManifestsResult as GeneratedScanAgentManifestsResult,
  AstroliftSkill as GeneratedSkill,
  AstroliftToolDef as GeneratedToolDef,
  RegisterAgentRepoInput as GeneratedRegisterAgentRepoInput,
} from "@/graphql/__generated__/schema";

export type AstroliftSkill = Pick<
  GeneratedSkill,
  | "id"
  | "name"
  | "slug"
  | "description"
  | "content"
  | "skillVersion"
  | "isGlobal"
  | "isActive"
  | "createdAt"
  | "updatedAt"
>;

export type AstroliftToolDef = Pick<
  GeneratedToolDef,
  | "id"
  | "name"
  | "slug"
  | "description"
  | "adapter"
  | "inputSchema"
  | "outputSchema"
  | "handlerRef"
  | "createdAt"
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

// Register-agent-repo wizard (PR-8). `scanAgentManifests` returns one of these
// per discovered manifest; the whole shape is queried so the facade is the
// whole type. `alreadyRegistered` drives the disabled-row state in the
// discovery step.
export type AstroliftDiscoveredAgentManifest = Pick<
  GeneratedDiscoveredAgentManifest,
  "manifestPath" | "name" | "slug" | "workloadKind" | "alreadyRegistered"
>;

export type AstroliftScanAgentManifestsResult = Omit<
  GeneratedScanAgentManifestsResult,
  "agents"
> & {
  agents: AstroliftDiscoveredAgentManifest[];
};

// One row of `registerAgentRepo`'s result — `created` is true for a
// freshly-registered agent, false for one that already existed (idempotent on
// the repo's manifest paths).
export type AstroliftRegisteredAgent = Pick<
  GeneratedRegisteredAgent,
  "manifestPath" | "slug" | "appId" | "workloadSlug" | "created"
>;

export type AstroliftRegisterAgentRepoResult = Omit<GeneratedRegisterAgentRepoResult, "agents"> & {
  agents: AstroliftRegisteredAgent[];
};

export type RegisterAgentRepoInput = GeneratedRegisterAgentRepoInput;
