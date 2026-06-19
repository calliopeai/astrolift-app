import type {
  AgentRunFamily as GeneratedAgentRunFamily,
  AgentRunMode as GeneratedAgentRunMode,
  AgentRunSpecInput as GeneratedAgentRunSpecInput,
  AstroliftAgentDetail as GeneratedAgentDetail,
  AstroliftAgentListItem as GeneratedAgentListItem,
  AstroliftAgentLiveStatus as GeneratedAgentLiveStatus,
  AstroliftAgentRunSpec as GeneratedAgentRunSpec,
  AstroliftAgentSkill as GeneratedAgentSkill,
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

// One ordered skill binding on an agent (spec 38 Phase 4), as returned by the
// `agent(slug)` detail join. Reuses the registry `AstroliftSkill` /
// `AstroliftToolDef` facades — `skill` is the bound skill, `toolDefs` are the
// tool definitions resolved for it on this agent. `position` is the ordering
// key the Build tab sorts by.
export type AstroliftAgentSkill = Pick<GeneratedAgentSkill, "position"> & {
  skill: AstroliftSkill;
  toolDefs: AstroliftToolDef[];
};

// Per-agent detail (spec 38 Phase 4) — the `agent(orgId, slug)` join the Build
// tab reads to compose brief + skills + tools alongside the build coordinates
// (`imageRef`/`dockerfilePath`). `brief` is nullable; `skills` is ordered by
// each binding's `position`. Distinct from `AstroliftAgentListItem` (the fleet
// list row) — this is the single-agent read with the joins attached.
export type AstroliftAgentDetail = Pick<
  GeneratedAgentDetail,
  | "id"
  | "name"
  | "slug"
  | "appSlug"
  | "sourceRepo"
  | "runFamily"
  | "runMode"
  | "runPaused"
  | "runCronExpression"
  | "imageRef"
  | "dockerfilePath"
> & {
  brief: AstroliftBrief | null;
  skills: AstroliftAgentSkill[];
};

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

// Run-spec editor (spec 33 PR-11/PR-12). The write surface for
// `updateAgentRunSpec`. The input enums are the UPPERCASE wire form
// (TASK|SERVICE, ONCE|LOOP|SCHEDULE|TRIGGER); note the read side
// (`AstroliftAgentListItem.runFamily/runMode` and the persisted spec below)
// returns the LOWERCASE stored value — the editor maps between the two.
export type AgentRunFamily = GeneratedAgentRunFamily;
export type AgentRunMode = GeneratedAgentRunMode;
export type AgentRunSpecInput = GeneratedAgentRunSpecInput;

// The persisted run-spec returned by `updateAgentRunSpec` (read-back after a
// write). Carries `replicas` (the Service baseline) — which the list-row
// (`AstroliftAgentListItem`) does NOT — so it is the only existing surface that
// exposes the stored replica count to the Service editor.
export type AstroliftAgentRunSpec = Pick<
  GeneratedAgentRunSpec,
  | "id"
  | "slug"
  | "kind"
  | "runFamily"
  | "runMode"
  | "runCronExpression"
  | "runPaused"
  | "runMaxParallel"
  | "replicas"
  | "scheduledScaleTo"
  | "scaleUpCron"
  | "scaleDownCron"
>;
