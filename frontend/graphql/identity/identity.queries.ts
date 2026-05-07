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

export const LIST_ROLES = gql`
  query ListRoles {
    astroliftRoles {
      id
      slug
      name
      description
      scopeLevel
      permissions
      isSystem
    }
  }
`;

export const LIST_MEMBERS = gql`
  query ListMembers {
    astroliftMembers {
      id
      user {
        id
        username
        email
        isActive
      }
      scopeKind
      scopeId
      isActive
      lifecycle
      joinedAt
      lastSeenAt
      createdAt
      deletedAt
    }
  }
`;

export const LIST_ROLE_BINDINGS = gql`
  query ListRoleBindings {
    astroliftRoleBindings {
      id
      user {
        id
        username
        email
      }
      groupExternalId
      role {
        id
        slug
        name
        scopeLevel
      }
      scopeKind
      scopeId
      grantedAt
      expiresAt
      inherits
    }
  }
`;

export const LIST_API_TOKENS = gql`
  query ListApiTokens {
    astroliftApiTokens {
      id
      name
      user {
        id
        username
        email
      }
      teamSlug
      tokenLast4
      scopes
      expiresAt
      lastUsedAt
      isRevoked
      createdAt
    }
  }
`;

// Suppress the unused import warning — fragment is referenced from
// other domain files once they're written.
void IDENTITY_TIMESTAMPS;
