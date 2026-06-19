import { gql } from "@apollo/client";

export const CREATE_SKILL = gql`
  mutation CreateSkill($orgId: ID!, $input: SkillInput!) {
    createSkill(orgId: $orgId, input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        name
        slug
        isActive
      }
    }
  }
`;

export const UPDATE_SKILL = gql`
  mutation UpdateSkill($id: ID!, $input: SkillInput!) {
    updateSkill(id: $id, input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        name
        slug
        isActive
      }
    }
  }
`;

export const DELETE_SKILL = gql`
  mutation DeleteSkill($id: ID!) {
    deleteSkill(id: $id) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const CREATE_TOOL_DEF = gql`
  mutation CreateToolDef($skillId: ID!, $input: ToolDefInput!) {
    createToolDef(skillId: $skillId, input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        name
        slug
        adapter
      }
    }
  }
`;

export const UPDATE_TOOL_DEF = gql`
  mutation UpdateToolDef($id: ID!, $input: ToolDefInput!) {
    updateToolDef(id: $id, input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        name
        slug
        adapter
      }
    }
  }
`;

export const DELETE_TOOL_DEF = gql`
  mutation DeleteToolDef($id: ID!) {
    deleteToolDef(id: $id) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

export const ASSEMBLE_BRIEF = gql`
  mutation AssembleBrief($skillIds: [ID!]!, $orgId: ID!, $config: JSON) {
    assembleBrief(skillIds: $skillIds, orgId: $orgId, config: $config) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ok
        briefId
      }
    }
  }
`;

export const LAUNCH_TASK = gql`
  mutation LaunchTask($briefId: ID!, $orgId: ID!, $callbackUrl: String) {
    launchTask(briefId: $briefId, orgId: $orgId, callbackUrl: $callbackUrl) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ok
        taskId
      }
    }
  }
`;

export const CANCEL_TASK = gql`
  mutation CancelTask($id: ID!) {
    cancelTask(id: $id) {
      ok
      errors {
        code
        message
        field
      }
    }
  }
`;

// Dispatch a registered agent for a single Once run (spec 33 PR-1/PR-10). This
// is the user-facing "Dispatch now" action — distinct from LAUNCH_TASK, which
// is Brief-based and never sets `agent_definition` or enqueues a workflow.
// `runAstroliftAgent` resolves the agentSlug to its Workload(kind=agent),
// creates an AgentTask wired to the Temporal dispatch pipeline, and returns it
// (id + status) so the caller can refetch the executions list and watch the
// new run appear with a live "running now" badge. Requires `agent.dispatch`.
// Closes #896 (the silent Dispatch no-op).
export const RUN_AGENT = gql`
  mutation RunAgent($input: RunAstroliftAgentInput!) {
    runAstroliftAgent(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        status
        createdAt
      }
    }
  }
`;

// ---------------------------------------------------------------------------
// Register-agent-repo wizard (PR-8). `registerAgentRepo` registers EVERY agent
// manifest discovered in the repo as an agent Workload under its own
// RegisteredApp — there is no per-manifest selection arg, so it is idempotent
// on (sourceRepo, manifestPath): `created` is true for freshly-registered
// agents and false for ones that already existed. The wizard's discovery
// checkboxes are therefore a client-side preview/confirm of what the repo will
// register, not a server-side filter.
// ---------------------------------------------------------------------------

export const REGISTER_AGENT_REPO = gql`
  mutation RegisterAgentRepo($input: RegisterAgentRepoInput!) {
    registerAgentRepo(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        agents {
          manifestPath
          slug
          appId
          workloadSlug
          created
        }
      }
    }
  }
`;

// Write the run-spec of a registered agent Workload(kind=agent) (spec 33
// PR-11/PR-12). One partial-update mutation backs both editors: only the
// fields the caller supplies are written, an omitted/null field is left
// unchanged — so an editor can save a single toggle without round-tripping the
// whole spec. PR-11 owns runFamily / runMode / runCronExpression / runPaused /
// replicas; the Loop/Trigger/scaled-scaling fields (runMaxParallel,
// scaleUpCron, scaleDownCron, scheduledScaleTo) are PR-12's and left unset by
// the Once/Schedule/Service editor. Returns the persisted run-spec so the
// editor reads back the stored state in one round-trip. Gates on `app.update`
// server-side (configuring an agent is an app-config write, distinct from
// `agent.dispatch`, which authorizes *running* it).
export const UPDATE_AGENT_RUN_SPEC = gql`
  mutation UpdateAgentRunSpec($agentSlug: String!, $input: AgentRunSpecInput!) {
    updateAgentRunSpec(agentSlug: $agentSlug, input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        kind
        runFamily
        runMode
        runCronExpression
        runPaused
        runMaxParallel
        replicas
        scheduledScaleTo
        scaleUpCron
        scaleDownCron
      }
    }
  }
`;

export const IMPORT_SKILLS_FROM_REPO = gql`
  mutation ImportSkillsFromRepo($repoUrl: String!, $branch: String) {
    importSkillsFromRepo(repoUrl: $repoUrl, branch: $branch) {
      ok
      errors {
        code
        message
        field
      }
      data {
        importedSkills
        importedTools
        sourceRef
      }
    }
  }
`;
