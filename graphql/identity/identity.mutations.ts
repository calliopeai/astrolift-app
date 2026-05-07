import { gql } from "@apollo/client";

export const CREATE_TEAM = gql`
  mutation CreateTeam($input: CreateTeamInput!) {
    createTeam(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
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
  }
`;

export const CREATE_PROJECT = gql`
  mutation CreateProject($input: CreateProjectInput!) {
    createProject(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
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
  }
`;

export const SOFT_DELETE_TEAM = gql`
  mutation SoftDeleteTeam($input: SoftDeleteByGuidInput!) {
    softDeleteTeam(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const SOFT_DELETE_PROJECT = gql`
  mutation SoftDeleteProject($input: SoftDeleteByGuidInput!) {
    softDeleteProject(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const UPDATE_ORGANIZATION = gql`
  mutation UpdateOrganization($input: UpdateOrganizationInput!) {
    updateOrganization(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        slug
        name
        website
        scimEnabled
        auditLogRetentionDays
        previewMaxActiveDefault
        logRetentionDaysDefault
      }
    }
  }
`;

export const CREATE_API_TOKEN = gql`
  mutation CreateApiToken($input: CreateApiTokenInput!) {
    createApiToken(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        plaintext
        apiToken {
          id
          name
          tokenLast4
          scopes
          expiresAt
          createdAt
          isRevoked
        }
      }
    }
  }
`;

export const REVOKE_API_TOKEN = gql`
  mutation RevokeApiToken($input: RevokeApiTokenInput!) {
    revokeApiToken(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;

export const GRANT_ROLE = gql`
  mutation GrantRole($input: GrantRoleInput!) {
    grantRole(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        user {
          id
          username
          email
        }
        role {
          id
          slug
          name
          scopeLevel
        }
        scopeKind
        scopeId
        grantedAt
      }
    }
  }
`;

export const REVOKE_ROLE_BINDING = gql`
  mutation RevokeRoleBinding($input: RevokeRoleBindingInput!) {
    revokeRoleBinding(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        deleted
      }
    }
  }
`;
