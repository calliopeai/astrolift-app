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
      onboardingCompletedAt
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
            isAgent
          }
        }
        unassignedApps {
          id
          slug
          name
          status
          isAgent
        }
      }
      unassignedApps {
        id
        slug
        name
        status
        isAgent
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
      onboardingCompletedAt
      createdAt
      updatedAt
      deletedAt
    }
  }
`;

/**
 * Lightweight query the dashboard issues to decide whether to
 * auto-open the onboarding wizard (#452). Returns only the bits the
 * decision needs: the org's onboardingCompletedAt + the count of
 * team memberships (zero teams + null timestamp → auto-open).
 * Picks up the active org from the tenant context middleware, so no
 * slug variable is needed.
 */
export const GET_ONBOARDING_STATE = gql`
  query GetOnboardingState {
    astroliftOrganizations {
      id
      slug
      name
      onboardingCompletedAt
    }
    astroliftTeams {
      id
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
  query ListMembers($search: String) {
    astroliftMembers(search: $search) {
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
      lastActiveAt
      createdAt
      deletedAt
    }
  }
`;

export const LIST_TEAM_MEMBERS = gql`
  query ListTeamMembers($teamId: GUID!) {
    astroliftTeamMembers(teamId: $teamId) {
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
      sourceScopeLabel
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
      createdByUsername
      updatedByUsername
      version
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
  activatedAt
  lastSwitchedByUsername
  version
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
      clientKind
      label
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
      timezone
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
      invitedByUserId
      invitedByDisplayName
      invitedByEmail
      invitedByAvatarUrl
      createdAt
    }
  }
`;

/**
 * Two-source de-dupe lookup the InviteDialog runs as the operator
 * types an email (#418). Returns existing org members AND pending
 * invitations matching the query; the FE narrows by ``matchKind`` to
 * render the right CTA ('grant role' vs. 'resend / cancel + reinvite').
 * Permission: ``org.manage_members``. Empty / whitespace query yields
 * an empty list — the FE debounces 300ms before issuing it so a fast
 * typist doesn't trigger a flurry of queries per keystroke.
 */
export const SEARCHABLE_USERS = gql`
  query SearchableUsers($query: String!) {
    astroliftSearchableUsers(query: $query) {
      matchKind
      email
      displayLabel
      avatarUrl
      userId
      invitationId
      invitationStatus
      expiresAt
    }
  }
`;

/**
 * Roles the active operator may grant on an invitation (#418). The
 * server filters by subset-of-effective-permissions so the FE can
 * skip rendering roles that would just be rejected at use time.
 * Returns an empty list when the operator holds no effective perms;
 * the InviteDialog disables with an explainer in that case. Django
 * superusers see every role.
 * Permission: ``org.manage_members``.
 */
export const LIST_ROLES_I_CAN_GRANT = gql`
  query ListRolesICanGrant {
    astroliftRolesICanGrant {
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

// #487 — step-up auth. The nav indicator polls this query so the
// "Admin elevated for N more minutes" badge stays accurate without
// requiring the user to refresh the page.
export const GET_ELEVATION_STATUS = gql`
  query GetElevationStatus {
    astroliftElevationStatus {
      elevated
      elevatedUntil
      secondsRemaining
      method
      requiredFor
    }
  }
`;
