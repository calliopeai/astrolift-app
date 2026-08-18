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
      agentSlug
      agentName
      projectSlug
      status
      callbackUrl
      result
      failureMessage
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
      failureMessage
      createdAt
      startedAt
      finishedAt
      vncEnabled
      vncUrl
      snapshotUrl
      podName
      namespace
    }
  }
`;

// Live fleet-map feed (#1091 — LiveFlowMap P2). Returns the org's AgentTasks
// whose state changed after `since` (the `updatedAt` cursor), oldest change
// first, capped. /fleet/map polls this with a moving high-water `since` and
// merges each batch into the map, so the map accumulates the live fleet and
// pulses an edge per transition. Carries the node-layer fields the map's
// dispatcher / cluster / agent layers render: the routing `dispatcher` (+ the
// cluster it spawns onto), the pod/namespace, and the lifecycle stamps.
export const AGENT_TASK_TRANSITIONS_SINCE = gql`
  query AgentTaskTransitionsSince($orgId: ID!, $since: DateTime, $limit: Int) {
    agentTaskTransitionsSince(orgId: $orgId, since: $since, limit: $limit) {
      id
      status
      createdAt
      updatedAt
      queuedAt
      provisioningAt
      startedAt
      finishedAt
      podName
      namespace
      dispatcher {
        id
        name
        slug
        cloud
        region
        clusterId
        clusterName
      }
    }
  }
`;

// Per-task interaction feed (#1092 — LiveFlowMap P3). Returns the control-plane-
// observed interactions for a single AgentTask — control_api calls + tool
// invocations today; gate/signal capture is deferred (#1217) — newest-relevant
// first, capped. The agent-run interaction map polls this while the run is live
// and rebuilds the graph on each fetch (P1-style full-set refetch, since=null),
// pulsing an edge per recent interaction. Tenant-scoped via `orgId` like the
// sibling fleet feed above. NOTE: this file is excluded from codegen operation
// typing (it interpolates the plain `AGENT_LIST_ITEM_FIELDS` constant, which
// graphql-tag-pluck can't resolve — see codegen.ts), so the Data/Vars shapes
// are hand-typed in agents.types.ts, exactly as the P2 transition feed is.
export const AGENT_TASK_INTERACTIONS = gql`
  query AgentTaskInteractions($orgId: ID!, $taskId: ID!, $since: DateTime, $limit: Int! = 200) {
    agentTaskInteractions(orgId: $orgId, taskId: $taskId, since: $since, limit: $limit) {
      id
      kind
      name
      status
      occurredAt
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

// Org-scoped AgentEnvironmentSpecs — the reusable container-environment recipes
// (image/runtime/tool-preset/VNC) an agent task can launch into. The Dispatch
// command center's Advanced section offers these as the `environmentSpecId`
// override on `runAstroliftAgent` (the recipe is where image + tools come from;
// the dispatch mutation accepts no ad-hoc image/skill/sizing fields).
export const LIST_AGENT_ENVIRONMENT_SPECS = gql`
  query ListAgentEnvironmentSpecs($orgId: ID!) {
    agentEnvironmentSpecs(orgId: $orgId) {
      id
      slug
      name
      runtime
      imageTag
      agentType
      vncEnabled
      secretRefs
      managedModel
    }
  }
`;

// Per-ref presence status for a spec's secret refs (#1173) — metadata only
// (envVar, uri, exists), never the value. The Manage-secrets dialog reads this
// to render Set / Missing chips and refetches it after a set/rotate/delete. A
// ref whose store read failed comes back exists=false with a short `error`.
export const AGENT_ENV_SPEC_SECRET_STATUS = gql`
  query AgentEnvironmentSpecSecretStatus($slug: String!) {
    agentEnvironmentSpecSecretStatus(slug: $slug) {
      envVar
      uri
      exists
      error
      provider
      canReveal
      readLimitation
    }
  }
`;

export const AGENT_SECRET_BUNDLES = gql`
  query AgentSecretBundles($slug: String!) {
    agentSecretBundles(envSpecSlug: $slug) {
      id
      slug
      name
      backendRef
      keyNames
      provider
      canReveal
      readLimitation
      createdAt
      updatedAt
    }
  }
`;

export const AGENT_SECRET_BUNDLE_ATTACHMENTS = gql`
  query AgentSecretBundleAttachments($slug: String!) {
    agentEnvironmentSpecSecretBundleAttachments(slug: $slug) {
      id
      bundleId
      bundleSlug
      bundleName
      environment
      prefix
      position
      keyNames
    }
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

// Per-agent detail join (spec 38 Phase 4). `agent(orgId, slug)` resolves a
// single registered agent and bundles the read-side joins the fleet list can't:
// the assembled `brief`, the ordered `skills` (each with its nested `toolDefs`),
// plus the build coordinates (`imageRef`/`dockerfilePath`). The agent-detail
// Build tab reads this to render brief + skills + tools (it was a "backend join
// pending" placeholder before the resolver landed). `brief` is nullable — an
// agent may have no assembled brief yet — and `skills` is ordered by `position`.
export const GET_AGENT_DETAIL = gql`
  query GetAgentDetail($orgId: ID!, $slug: String!) {
    agent(orgId: $orgId, slug: $slug) {
      id
      name
      slug
      appSlug
      sourceRepo
      runFamily
      runMode
      runPaused
      runCronExpression
      imageRef
      dockerfilePath
      brief {
        id
        contentHash
        storageKey
        config
        createdAt
      }
      skills {
        position
        skill {
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
        toolDefs {
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

// Trigger-binding editor (spec 33 PR-6/PR-12; #951). The webhook bindings that
// make an agent dispatch on a matching SCM push — one row per bound
// WorkflowWebhook. Each carries the delivery `endpoint`, the
// `scmRepo`/`branchPattern` it fires on, the `inputMapping` applied to the
// payload, plus `enabled` + `lastTriggeredAt` for the row's status. The Control
// tab's Trigger section reads this and refetches it after a create/unbind.
// Tenant-scoped via `orgId`. Hand-typed Data/Vars (AgentTriggersData/Vars) live
// in agents.types.ts — this file is excluded from codegen (see codegen.ts).
export const AGENT_TRIGGERS = gql`
  query AgentTriggers($orgId: ID!, $agentSlug: String!) {
    agentTriggers(orgId: $orgId, agentSlug: $agentSlug) {
      slug
      endpoint
      scmRepo
      branchPattern
      inputMapping
      enabled
      lastTriggeredAt
      createdAt
    }
  }
`;

// ---------------------------------------------------------------------------
// Agent boxes (#128) — long-lived containers that exist to be exec'd into.
// Defaults to the live ones; `includeEnded` also returns the settled rows so
// an operator can see a box was idle-reaped rather than never started.
// ---------------------------------------------------------------------------

export const LIST_AGENT_BOXES = gql`
  query ListAgentBoxes($orgId: ID!, $includeEnded: Boolean) {
    agentBoxes(orgId: $orgId, includeEnded: $includeEnded) {
      id
      name
      slug
      status
      agentSlug
      environmentSpecSlug
      image
      idleTimeoutSeconds
      sessionName
      attachCommand
      namespace
      podName
      ownerEmail
      lastError
      startedAt
      endedAt
      lastAttachedAt
      createdAt
    }
  }
`;

