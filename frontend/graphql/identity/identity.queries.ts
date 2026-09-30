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
      appearanceDefault
      appearanceLocked
      restrictedSettingsDefault
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

/**
 * Cursor-paginated companions to the ``LIST_*`` documents above (#1230).
 *
 * Every one of them speaks the platform page envelope — ``{ items,
 * nextCursor, totalCount }`` in, ``limit`` + ``after`` in — which is the
 * shape ``useCursorTable`` consumes, so a table never has to fetch a
 * capped list and slice it in the browser. The unpaginated siblings stay
 * exported while the surfaces that still read them are migrated; delete
 * each one with its last consumer and the row fragment stops being
 * duplicated.
 *
 * Row selections are named fragments rather than plain template-literal
 * constants on purpose: ``graphql-tag-pluck`` cannot resolve a bare
 * ``${FIELDS}`` interpolation, which is why this whole file is excluded
 * from codegen today (see ``codegen.ts``). Spreadable fragments are the
 * documented way back in.
 *
 * The identity lists (teams, roles, members, bindings, policies,
 * invitations) also take the list contract (#2153): ``filter``, ``sort``,
 * ``page`` and ``pageSize``, declared on each document below.
 */
const TEAM_FIELDS = gql`
  fragment TeamFields on AstroliftTeam {
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
`;

/**
 * On the list contract (#2153): ``filter`` (``mine``), ``sort`` (name, slug,
 * created) and ``page`` / ``pageSize``. Any of the last three selects
 * numbered paging with an exact ``totalCount``; without them it is the
 * cursor walk the pickers use.
 */
export const LIST_TEAMS_PAGE = gql`
  ${TEAM_FIELDS}
  query ListTeamsPage(
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftTeamsListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftTeamsPage(
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...TeamFields
      }
      nextCursor
      totalCount
      page
      pageSize
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

const PROJECT_FIELDS = gql`
  fragment ProjectFields on AstroliftProject {
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
`;

export const LIST_PROJECTS_PAGE = gql`
  ${PROJECT_FIELDS}
  query ListProjectsPage($search: String, $limit: Int, $after: String) {
    astroliftProjectsPage(search: $search, limit: $limit, after: $after) {
      items {
        ...ProjectFields
      }
      nextCursor
      totalCount
    }
  }
`;

// Live slug-availability checks backing the team / project rename
// forms. Return a bare Boolean: true only when the slug is a valid
// slug AND free within its scope (team slugs are unique per org,
// project slugs per team). ``excludeId`` is the guid of the row being
// edited so keeping its current slug reads as available.
export const TEAM_SLUG_AVAILABLE = gql`
  query TeamSlugAvailable($slug: String!, $excludeId: GUID) {
    astroliftTeamSlugAvailable(slug: $slug, excludeId: $excludeId)
  }
`;

export const PROJECT_SLUG_AVAILABLE = gql`
  query ProjectSlugAvailable($teamId: GUID!, $slug: String!, $excludeId: GUID) {
    astroliftProjectSlugAvailable(teamId: $teamId, slug: $slug, excludeId: $excludeId)
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
            primitiveKind
            primitiveSlug
          }
          workflows {
            id
            slug
            name
            isEnabled
            childWorkflowIds
            agents {
              id
              slug
              name
              status
              primitiveKind
              primitiveSlug
            }
          }
          standaloneAgents {
            id
            slug
            name
            status
            primitiveKind
            primitiveSlug
          }
        }
        unassignedApps {
          id
          slug
          name
          status
          primitiveKind
          primitiveSlug
        }
      }
      unassignedApps {
        id
        slug
        name
        status
        primitiveKind
        primitiveSlug
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
      appearanceDefault
      appearanceLocked
      restrictedSettingsDefault
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

const ROLE_FIELDS = gql`
  fragment RoleFields on AstroliftRole {
    id
    slug
    name
    description
    scopeLevel
    permissions
    isSystem
  }
`;

/**
 * On the list contract (#2153): ``filter`` (isSystem, scopeLevel,
 * createdBy), ``sort`` (name, slug, created, scopeLevel, bindings) and
 * numbered pages. ``bindingsCount`` counts this org's bindings only.
 */
export const LIST_ROLES_PAGE = gql`
  ${ROLE_FIELDS}
  query ListRolesPage(
    $search: String
    $limit: Int
    $after: String
    $sortBy: AstroliftListSortKey
    $filter: AstroliftRolesListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftRolesPage(
      search: $search
      limit: $limit
      after: $after
      sortBy: $sortBy
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...RoleFields
        bindingsCount
        duplicatedFrom {
          id
          slug
          name
          isSystem
          deleted
        }
      }
      nextCursor
      totalCount
      page
      pageSize
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
      teamId
      teamSlug
      teamName
    }
  }
`;

const MEMBER_FIELDS = gql`
  fragment MemberFields on AstroliftMember {
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
`;

/**
 * On the list contract (#2153): ``filter`` (scopeKind, lifecycle, role,
 * team, mine, admin, active), ``sort`` (name, email, created, joined,
 * lifecycle, lastActive, roles) and numbered pages. ``scopeKind: ["ORG"]``
 * is one row per person; ``teams`` is set on this query only.
 */
export const LIST_MEMBERS_PAGE = gql`
  ${MEMBER_FIELDS}
  query ListMembersPage(
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftMembersListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftMembersPage(
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...MemberFields
        teamId
        teamSlug
        teamName
        teams {
          id
          slug
          name
        }
      }
      nextCursor
      totalCount
      page
      pageSize
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

const ROLE_BINDING_FIELDS = gql`
  fragment RoleBindingFields on AstroliftRoleBinding {
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
`;

/**
 * ``appSlug`` narrows to the bindings scoped to one app, which is what the
 * app Members tab shows. The argument shipped on the field in #1241 and no
 * document declared it, so that surface went on client-filtering the
 * org-wide list. Omitted, the field is org-wide, which is what the
 * assignments tab wants.
 */
export const LIST_ROLE_BINDINGS_PAGE = gql`
  ${ROLE_BINDING_FIELDS}
  query ListRoleBindingsPage(
    $search: String
    $appSlug: String
    $limit: Int
    $after: String
    $roleId: GUID
    $filter: AstroliftRoleBindingsListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftRoleBindingsPage(
      search: $search
      appSlug: $appSlug
      limit: $limit
      after: $after
      roleId: $roleId
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...RoleBindingFields
      }
      nextCursor
      totalCount
      page
      pageSize
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

const POLICY_FIELDS = gql`
  fragment PolicyFields on AstroliftPolicy {
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
`;

/**
 * On the list contract (#2153): ``filter`` (effect, scopeLevel, createdBy),
 * ``sort`` (name, slug, created, updated, effect, scopeLevel) and numbered
 * pages.
 */
export const LIST_POLICIES_PAGE = gql`
  ${POLICY_FIELDS}
  query ListPoliciesPage(
    $search: String
    $limit: Int
    $after: String
    $sortBy: AstroliftListSortKey
    $filter: AstroliftPoliciesListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftPoliciesPage(
      search: $search
      limit: $limit
      after: $after
      sortBy: $sortBy
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...PolicyFields
      }
      nextCursor
      totalCount
      page
      pageSize
    }
  }
`;

const IDP_FIELDS = gql`
  fragment IdentityProviderFields on AstroliftIdentityProvider {
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
  }
`;

export const LIST_IDENTITY_PROVIDERS = gql`
  ${IDP_FIELDS}
  query ListIdentityProviders {
    astroliftIdentityProviders {
      ...IdentityProviderFields
    }
  }
`;

export const GET_ACTIVE_IDENTITY_PROVIDER = gql`
  ${IDP_FIELDS}
  query GetActiveIdentityProvider {
    astroliftActiveIdentityProvider {
      ...IdentityProviderFields
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

const API_TOKEN_FIELDS = gql`
  fragment ApiTokenFields on AstroliftApiToken {
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
    effectivePermissions
  }
`;

// The token picker's only source of scopes (#2120): what each one unlocks,
// derived from enforcement, and whether the caller's roles let them use it.
export const GET_API_TOKEN_SCOPE_CATALOG = gql`
  query GetApiTokenScopeCatalog {
    astroliftApiTokenScopeCatalog {
      scopes {
        value
        label
        surface
        description
        sensitive
        permissions
        available
        unavailableReason
      }
      presets {
        key
        label
        scopes
      }
    }
  }
`;

export const LIST_API_TOKENS_PAGE = gql`
  ${API_TOKEN_FIELDS}
  query ListApiTokensPage($search: String, $limit: Int, $after: String) {
    astroliftApiTokensPage(search: $search, limit: $limit, after: $after) {
      items {
        ...ApiTokenFields
      }
      nextCursor
      totalCount
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

const INVITATION_FIELDS = gql`
  fragment InvitationFields on AstroliftInvitation {
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
    lastActiveAt
  }
`;

// ``status`` is the same single-value filter ``LIST_INVITATIONS`` takes
// (pending / accepted / …), kept as a static variable so a status tab
// resets the cursor walk rather than paging through a stale question.
export const LIST_INVITATIONS_PAGE = gql`
  ${INVITATION_FIELDS}
  query ListInvitationsPage(
    $status: String
    $search: String
    $limit: Int
    $after: String
    $filter: AstroliftInvitationsListFilter
    $sort: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftInvitationsPage(
      status: $status
      search: $search
      limit: $limit
      after: $after
      filter: $filter
      sort: $sort
      page: $page
      pageSize: $pageSize
    ) {
      items {
        ...InvitationFields
      }
      nextCursor
      totalCount
      page
      pageSize
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

/** Complete matching lists: paging variables never truncate these exports. */
export const EXPORT_MEMBERS_CSV = gql`
  query ExportMembersCsv($search: String, $filter: AstroliftMembersListFilter, $sort: String) {
    astroliftMembersCsv(search: $search, filter: $filter, sort: $sort) {
      filename
      content
      rowCount
      contentType
    }
  }
`;
export const EXPORT_INVITATIONS_CSV = gql`
  query ExportInvitationsCsv(
    $status: String
    $search: String
    $filter: AstroliftInvitationsListFilter
    $sort: String
  ) {
    astroliftInvitationsCsv(status: $status, search: $search, filter: $filter, sort: $sort) {
      filename
      content
      rowCount
      contentType
    }
  }
`;
export const EXPORT_ROLE_BINDINGS_CSV = gql`
  query ExportRoleBindingsCsv(
    $search: String
    $appSlug: String
    $roleId: GUID
    $filter: AstroliftRoleBindingsListFilter
    $sort: String
  ) {
    astroliftRoleBindingsCsv(
      search: $search
      appSlug: $appSlug
      roleId: $roleId
      filter: $filter
      sort: $sort
    ) {
      filename
      content
      rowCount
      contentType
    }
  }
`;
