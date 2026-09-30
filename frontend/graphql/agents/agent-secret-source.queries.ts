import { gql } from "@apollo/client";

export const AGENT_SECRET_SOURCE_OPTIONS = gql`
  query AgentSecretSourceOptions($orgId: ID!, $search: String, $page: Int!, $pageSize: Int!) {
    agentEnvironmentSpecsPage(
      orgId: $orgId
      search: $search
      sort: "slug"
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        slug
        name
        teamId
        projectId
      }
      totalCount
      page
      pageSize
    }
  }
`;

export const AGENT_SECRET_SOURCE_DETAIL = gql`
  query AgentSecretSourceDetail($orgId: ID!, $slug: String!) {
    agentEnvironmentSpec(orgId: $orgId, slug: $slug) {
      id
      slug
      name
      teamId
      projectId
    }
  }
`;
