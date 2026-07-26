import type {
  AgentRunFamily as GeneratedAgentRunFamily,
  AgentRunMode as GeneratedAgentRunMode,
  AgentRunSpecInput as GeneratedAgentRunSpecInput,
  AstroliftAgentDetail as GeneratedAgentDetail,
  AstroliftAgentEnvironmentSpec as GeneratedAgentEnvironmentSpec,
  AstroliftAgentInteraction as GeneratedAgentInteraction,
  AstroliftAgentListItem as GeneratedAgentListItem,
  AstroliftAgentLiveStatus as GeneratedAgentLiveStatus,
  AstroliftAgentRunSpec as GeneratedAgentRunSpec,
  AstroliftAgentSecretStatus as GeneratedAgentSecretStatus,
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

// One AgentTask (a run). Carries the VNC coordinates (`vncEnabled` / `vncUrl` /
// `snapshotUrl`) alongside the status/timestamps so the run detail view can
// render the "watch live" affordance, plus the pod coordinates (`podName` /
// `namespace`) and `failureMessage` — the human-readable spawn/dispatch failure
// reason (null unless the run failed). A spawn-failed run never creates a pod,
// so its `agentTaskLogs` is empty and `failureMessage` is the only debug signal.
// NOTE the read type exposes no run-mode or trigger-payload field — those live
// on the agent Workload, not the task — so the detail surface shows only what
// the task actually carries.
export type AstroliftAgentTask = Pick<
  GeneratedAgentTask,
  | "id"
  | "status"
  | "callbackUrl"
  | "result"
  | "failureMessage"
  | "createdAt"
  | "startedAt"
  | "finishedAt"
  | "vncEnabled"
  | "vncUrl"
  | "snapshotUrl"
  | "podName"
  | "namespace"
>;

// ── Fleet map (#1091 — LiveFlowMap P2): live dispatch feed ───────────────────
// Hand-typed against schema.graphql (like the tiered-workflows types), not
// faceted over __generated__: the fleet map is a self-contained poll surface
// and these node-layer fields lag the committed codegen output. `dispatcher`
// is null until the Controller routes the task at PROVISIONING.

// The dispatcher an AgentTask was routed to, plus the cluster it spawns onto.
// `clusterId` correlates the task to its cluster-layer node + heartbeat
// liveness on the map; null when the dispatcher runs standalone.
export type FleetTaskDispatcher = {
  id: string;
  name: string;
  slug: string;
  cloud: string;
  region: string;
  clusterId: string | null;
  clusterName: string;
};

// One AgentTask as projected by `agentTaskTransitionsSince`. `updatedAt` is the
// cursor the map advances across polls; the queued/provisioning/started/
// finished stamps drive per-stage timing; `podName` / `namespace` label the
// agent node.
export type AgentTaskTransition = {
  id: string;
  status: string;
  createdAt: string;
  updatedAt: string;
  queuedAt: string | null;
  provisioningAt: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  podName: string;
  namespace: string;
  dispatcher: FleetTaskDispatcher | null;
};

export type AgentTaskTransitionsSinceData = {
  agentTaskTransitionsSince: AgentTaskTransition[];
};

export type AgentTaskTransitionsSinceVars = {
  orgId: string;
  since?: string | null;
  limit?: number | null;
};

// ── Agent interaction map (#1092 — LiveFlowMap P3): per-task interaction feed ─
// Faceted over the committed generated type (the `agentTaskInteractions` SDL
// landed in ccf13f4, so codegen is authoritative here — unlike the P2 transition
// feed above, hand-typed because its node-layer fields lag codegen). One row per
// control-plane-observed interaction on a single AgentTask: `kind` groups the
// target (control_api | tool_call | signal | gate), `name` is the specific
// endpoint/tool (e.g. "callback"/"checkin"/"meter"), `status` is ok/error, and
// `occurredAt` drives the map's recency/pulse. gate + signal are not captured
// yet (#1217) — the map renders those kinds as "capture pending" placeholders.
export type AstroliftAgentInteraction = Pick<
  GeneratedAgentInteraction,
  "id" | "kind" | "name" | "status" | "occurredAt"
>;

export type AgentTaskInteractionsData = {
  agentTaskInteractions: AstroliftAgentInteraction[];
};

export type AgentTaskInteractionsVars = {
  orgId: string;
  taskId: string;
  since?: string | null;
  limit?: number | null;
};

// A reusable agent container-environment recipe (org-scoped), as offered in the
// Dispatch command center's Advanced section for the `environmentSpecId`
// override. `runtime` / `imageTag` describe the base image the recipe resolves.
export type AstroliftAgentEnvironmentSpec = Pick<
  GeneratedAgentEnvironmentSpec,
  | "id"
  | "slug"
  | "name"
  | "runtime"
  | "imageTag"
  | "agentType"
  | "vncEnabled"
  | "secretRefs"
  | "managedModel"
>;

// Per-ref secret presence status (#1173) as returned by
// `agentEnvironmentSpecSecretStatus`. Metadata only — `exists` is the store
// presence, `error` a short driver message when the check couldn't complete.
// The VALUE is never on this type.
export type AstroliftAgentSecretStatus = Pick<
  GeneratedAgentSecretStatus,
  "envVar" | "uri" | "exists" | "error"
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
