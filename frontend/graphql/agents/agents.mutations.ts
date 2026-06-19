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
