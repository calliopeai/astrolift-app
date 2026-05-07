import { gql } from "@apollo/client";

const IDENTITY_TIMESTAMPS = gql`
  fragment IdentityTimestamps on AstroliftOrganization {
    createdAt
    updatedAt
    deletedAt
  }
`;

export const LIST_ORGANIZATIONS = gql`
  query ListOrganizations {
    astroliftOrganizations {
      id
      slug
      name
      website
      scimEnabled
      auditLogRetentionDays
      previewMaxActiveDefault
      logRetentionDaysDefault
      createdAt
      updatedAt
      deletedAt
    }
  }
`;

export const LIST_TEAMS = gql`
  query ListTeams {
    astroliftTeams {
      id
      slug
      name
      organization {
        id
        slug
        name
      }
      createdAt
      updatedAt
      deletedAt
    }
  }
`;

export const LIST_PROJECTS = gql`
  query ListProjects {
    astroliftProjects {
      id
      slug
      name
      organization {
        id
        slug
        name
      }
      team {
        id
        slug
        name
      }
      createdAt
      updatedAt
      deletedAt
    }
  }
`;

export const GET_ORGANIZATION = gql`
  query GetOrganization($slug: String!) {
    astroliftOrganization(slug: $slug) {
      id
      slug
      name
      website
      scimEnabled
      auditLogRetentionDays
      previewMaxActiveDefault
      logRetentionDaysDefault
      createdAt
      updatedAt
      deletedAt
    }
  }
`;

// Suppress the unused import warning — fragment is referenced from
// other domain files once they're written.
void IDENTITY_TIMESTAMPS;
