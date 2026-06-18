import { gql } from "@apollo/client";

export const LIST_SKILLS = gql`
  query ListSkills($orgId: ID!, $isGlobal: Boolean) {
    skills(orgId: $orgId, isGlobal: $isGlobal) {
      id
      name
      slug
      description
      content
      skillVersion
      isGlobal
      isActive
      createdAt
      updatedAt
    }
  }
`;

export const GET_SKILL = gql`
  query GetSkill($id: ID!) {
    skill(id: $id) {
      id
      name
      slug
      description
      content
      skillVersion
      isGlobal
      isActive
      createdAt
      updatedAt
    }
  }
`;

export const LIST_TOOL_DEFS = gql`
  query ListToolDefs($skillId: ID!) {
    toolDefs(skillId: $skillId) {
      id
      name
      slug
      description
      adapter
      inputSchema
      outputSchema
      handlerRef
      createdAt
    }
  }
`;

export const LIST_ORG_TOOL_DEFS = gql`
  query ListOrgToolDefs($orgId: ID!) {
    orgToolDefs(orgId: $orgId) {
      id
      name
      slug
      description
      adapter
      inputSchema
      outputSchema
      handlerRef
      createdAt
    }
  }
`;

export const GET_BRIEF = gql`
  query GetBrief($id: ID!) {
    brief(id: $id) {
      id
      contentHash
      storageKey
      config
      createdAt
    }
  }
`;

// Agent runs/executions. `workloadId` (PR-2) scopes the list to a single
// agent Workload — the per-agent Executions list (spec 33 PR-10) passes it;
// the org-wide fleet surfaces (Active / History tabs, Observe) leave it null
// and get every agent's tasks. `status` narrows by run state when set.
export const LIST_AGENT_TASKS = gql`
  query ListAgentTasks($orgId: ID!, $status: String, $workloadId: ID) {
    agentTasks(orgId: $orgId, status: $status, workloadId: $workloadId) {
      id
      status
      callbackUrl
      result
      createdAt
      startedAt
      finishedAt
      vncEnabled
      vncUrl
      snapshotUrl
    }
  }
`;

export const GET_AGENT_TASK = gql`
  query GetAgentTask($id: ID!) {
    agentTask(id: $id) {
      id
      status
      callbackUrl
      result
      createdAt
      startedAt
      finishedAt
      vncEnabled
      vncUrl
      snapshotUrl
    }
  }
`;

// Per-run log lines for a single agent task, newest `tail` lines. Keyed by
// the AGENT TASK id (not workloadId) — the agent-detail Observe tab reads
// these for the most-recent run. NOTE: `agentTaskLogs` returns an empty list
// on real clusters today (#891 — the log relay isn't wired through); the
// Observe surface renders that gracefully rather than treating empty as an
// error.
export const AGENT_TASK_LOGS = gql`
  query AgentTaskLogs($id: ID!, $tail: Int) {
    agentTaskLogs(id: $id, tail: $tail)
  }
`;

// ---------------------------------------------------------------------------
// Registry list (PR-7) — registered AGENTS (not runs), project-scoped or
// fleet-wide. `agentWorkloads` is the per-project list; `agentFleet` is the
// org-wide roll-up. Both return the same `AstroliftAgentListItem` row shape.
// `agentLiveStatus` is the volatile companion signal (running count,
// next-scheduled, paused/idle) merged into rows by workloadId.
// ---------------------------------------------------------------------------

const AGENT_LIST_ITEM_FIELDS = `
  id
  name
  slug
  appSlug
  projectSlug
  sourceRepo
  sourceUrl
  runFamily
  runMode
  runPaused
  runCronExpression
  lastRunStatus
  lastRunAt
  runningCount
`;

export const LIST_AGENT_WORKLOADS = gql`
  query ListAgentWorkloads($orgId: ID!, $projectSlug: String) {
    agentWorkloads(orgId: $orgId, projectSlug: $projectSlug) {
      ${AGENT_LIST_ITEM_FIELDS}
    }
  }
`;

export const LIST_AGENT_FLEET = gql`
  query ListAgentFleet($orgId: ID!) {
    agentFleet(orgId: $orgId) {
      ${AGENT_LIST_ITEM_FIELDS}
    }
  }
`;

export const LIST_AGENT_LIVE_STATUS = gql`
  query ListAgentLiveStatus($orgId: ID!, $projectSlug: String, $workloadId: ID) {
    agentLiveStatus(orgId: $orgId, projectSlug: $projectSlug, workloadId: $workloadId) {
      workloadId
      workloadSlug
      appSlug
      runFamily
      runMode
      isPaused
      isIdle
      runningCount
      lastRunStatus
      lastRunAt
      nextScheduledAt
    }
  }
`;

// ---------------------------------------------------------------------------
// Register-agent-repo wizard (PR-8) — repo discovery preview. `scanAgentManifests`
// walks the repo (monorepo `agents/*/astrolift.toml` + a root `astrolift.toml`)
// and returns each agent manifest as a preview row WITHOUT persisting anything.
// `alreadyRegistered` is computed against the caller's org so the wizard can
// render already-registered rows as disabled. Registration is confirmed via
// `REGISTER_AGENT_REPO` (agents.mutations).
// ---------------------------------------------------------------------------

export const SCAN_AGENT_MANIFESTS = gql`
  query ScanAgentManifests($orgId: ID!, $sourceRepo: String!, $sourceKind: String!, $ref: String!) {
    scanAgentManifests(orgId: $orgId, sourceRepo: $sourceRepo, sourceKind: $sourceKind, ref: $ref) {
      ok
      error
      agents {
        manifestPath
        name
        slug
        workloadKind
        alreadyRegistered
      }
    }
  }
`;

// The VNC theatre roster: RUNNING, watchable agent tasks (vnc-capable
// with a published relay path). Gated on agent_task.watch server-side.
// Each row carries vncUrl (the live RFB relay the theatre connects to)
// and snapshotUrl (a short-lived presigned GET the gallery tiles poll).
export const AGENT_GALLERY = gql`
  query AgentGallery($orgId: ID!) {
    agentGallery(orgId: $orgId) {
      id
      status
      startedAt
      vncEnabled
      vncUrl
      snapshotUrl
    }
  }
`;
