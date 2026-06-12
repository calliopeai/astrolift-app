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

export const LIST_AGENT_TASKS = gql`
  query ListAgentTasks($orgId: ID!, $status: String) {
    agentTasks(orgId: $orgId, status: $status) {
      id
      status
      callbackUrl
      result
      createdAt
      startedAt
      finishedAt
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
    }
  }
`;

