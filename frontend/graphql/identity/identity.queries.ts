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
      allowUserProfileEdit
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

export const LIST_NAV_TREE = gql`
  query ListNavTree {
    astroliftNavTree {
      organization {
        id
        slug
        name
      }
      teams {
        team {
          id
          slug
          name
        }
        projects {
          project {
            id
            slug
            name
          }
          apps {
            id
            slug
            name
            status
          }
        }
        unassignedApps {
          id
          slug
          name
          status
        }
      }
      unassignedApps {
        id
        slug
        name
        status
      }
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
      allowUserProfileEdit
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

export const LIST_POLICIES = gql`
  query ListPolicies {
    astroliftPolicies {
      id
      slug
      name
      description
      scopeLevel
      scopeId
      effect
      actionPattern
      resourcePattern
      conditions
      actorPattern
      createdAt
      updatedAt
      deletedAt
    }
  }
`;

const IDP_FIELDS = `
  id
  organizationSlug
  kind
  name
  config
  metadataUrl
  oidcDiscoveryUrl
  clientId
  isDefault
  isActive
  createdAt
  updatedAt
`;

export const LIST_IDENTITY_PROVIDERS = gql`
  query ListIdentityProviders {
    astroliftIdentityProviders {
      ${IDP_FIELDS}
    }
  }
`;

export const GET_ACTIVE_IDENTITY_PROVIDER = gql`
  query GetActiveIdentityProvider {
    astroliftActiveIdentityProvider {
      ${IDP_FIELDS}
    }
  }
`;

export const LIST_ACTIVE_SESSIONS = gql`
  query ListActiveSessions {
    astroliftActiveSessions {
      id
      expiresAt
      isCurrent
      createdAt
      lastSeenAt
      ipAddress
      userAgent
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
      lastUsedIp
      lastUsedAgent
      isRevoked
      createdAt
    }
  }
`;

export const GET_MY_PROFILE = gql`
  query GetMyProfile {
    astroliftMyProfile {
      userId
      username
      firstName
      lastName
      email
      lockedFields
      orgAllowsEdit
    }
  }
`;

export const LIST_ORGANIZATION_ALLOWLIST_DOMAINS = gql`
  query ListOrganizationAllowlistDomains {
    astroliftOrganizationAllowlistDomains {
      id
      domain
      defaultRoleSlug
      requiresReview
      createdAt
      updatedAt
    }
  }
`;

export const LIST_INVITATIONS = gql`
  query ListInvitations($status: String) {
    astroliftInvitations(status: $status) {
      id
      email
      scopeKind
      scopeId
      roleSlug
      status
      expiresAt
      acceptedAt
      invitedByUsername
      createdAt
    }
  }
`;

/**
 * Active org-member set, shaped for the approval-policy picker (#410).
 * Reused by the register-app wizard and any later surface that needs a
 * user-picker scoped to an org. Permission: `org.manage_members`.
 */
export const ORG_MEMBERS_FOR_APPROVAL_PICKER = gql`
  query OrgMembersForApprovalPicker($orgSlug: String!) {
    astroliftOrgMembersForApprovalPicker(orgSlug: $orgSlug) {
      id
      email
      displayName
      avatarUrl
    }
  }
`;

export const LIST_MY_CONNECTED_ACCOUNTS = gql`
  query ListMyConnectedAccounts {
    astroliftMyConnectedAccounts {
      providerConfigId
      providerKind
      providerLabel
      isConnected
      linkedAccountLogin
      reauthRequired
      expiresAt
      lastUsedAt
    }
  }
`;

// Suppress the unused import warning — fragment is referenced from
// other domain files once they're written.
void IDENTITY_TIMESTAMPS;
