import { gql } from "@apollo/client";

/**
 * Permission analysis (core/schema/types/permission_analysis.py): the
 * resolver's own answer and trace, for Admin › Access › Check access.
 * Self, superuser or `org.manage_members`; both take an optional target
 * scope (`scopeType` ORG, TEAM, PROJECT, APP or AGENT, `scopeId` its guid),
 * so the answer is for that object, not anywhere in the org.
 */
export const PERMISSION_DIAGNOSE = gql`
  query PermissionDiagnose(
    $userId: ID!
    $permission: String!
    $scopeType: String
    $scopeId: String
  ) {
    permissionDiagnose(
      userId: $userId
      permission: $permission
      scopeType: $scopeType
      scopeId: $scopeId
    ) {
      userId
      username
      permission
      granted
      isSuperuser
      steps {
        check
        result
        detail
      }
    }
  }
`;

export const PERMISSION_COMPARE = gql`
  query PermissionCompare($userIdA: ID!, $userIdB: ID!, $scopeType: String, $scopeId: String) {
    permissionCompare(
      userIdA: $userIdA
      userIdB: $userIdB
      scopeType: $scopeType
      scopeId: $scopeId
    ) {
      userAUsername
      userBUsername
      onlyA
      onlyB
      shared
    }
  }
`;

/**
 * People, IdP groups, teams and invitations in one numbered search
 * (#2126): the Grant access flow's Who step and the People list's Groups
 * view. `counts` gives the matches per kind searched. Rows come users,
 * groups, teams, invitations, then by name. Needs `org.manage_members`.
 */
export const PRINCIPAL_SEARCH = gql`
  query PrincipalSearch(
    $search: String
    $filter: AstroliftPrincipalSearchFilter
    $page: Int
    $pageSize: Int
  ) {
    astroliftPrincipalSearch(search: $search, filter: $filter, page: $page, pageSize: $pageSize) {
      items {
        kind
        key
        name
        secondary
        userId
        memberId
        lifecycle
        avatarUrl
        groupExternalId
        memberCount
        bindingsCount
        mappingsCount
        teamId
        teamSlug
        invitationId
        invitationStatus
        expiresAt
      }
      totalCount
      page
      pageSize
      counts {
        kind
        count
      }
    }
  }
`;

const GRANT_PREVIEW_PERSON = gql`
  fragment GrantPreviewPersonFields on AstroliftGrantPreviewPerson {
    user {
      id
      username
      email
    }
    memberId
    through
    gained
    lost
    kept
    via {
      source
      bindingId
      roleSlug
      roleName
      scopeKind
      scopeGuid
      sourceScopeLabel
      groupExternalId
      teamSlug
      inherited
      permissions
    }
  }
`;

/**
 * What a grant, change or removal would do before it is written (#2126):
 * who gains, loses or keeps which permissions and through what, the grant
 * ceiling (`allowed`, `refusal`), and the IdP groups it reaches. Needs
 * `org.manage_members`.
 */
export const GRANT_PREVIEW = gql`
  ${GRANT_PREVIEW_PERSON}
  query GrantPreview($input: AstroliftGrantPreviewInput!, $limit: Int) {
    astroliftGrantPreview(input: $input, limit: $limit) {
      ok
      errors
      action
      permissions
      scopeKind
      scopeGuid
      sourceScopeLabel
      summary
      gainingCount
      losingCount
      unchangedCount
      gaining {
        ...GrantPreviewPersonFields
      }
      losing {
        ...GrantPreviewPersonFields
      }
      unchanged {
        ...GrantPreviewPersonFields
      }
      groups {
        groupExternalId
        memberCount
      }
      allowed
      refusal
      notes
    }
  }
`;

/**
 * The condition kinds, resource and actor keys a policy can use (#2126),
 * from the same tables the resolver evaluates. Needs `org.read`.
 */
export const POLICY_CONDITION_CATALOG = gql`
  query PolicyConditionCatalog {
    astroliftPolicyConditionCatalog {
      conditions {
        kind
        label
        description
        needs
        fields {
          name
          type
          label
          description
          required
          default
          options
          minimum
        }
        example
      }
      resourceKeys {
        key
        label
        description
      }
      actorKeys {
        key
        label
        description
      }
      effects
      scopeLevels
    }
  }
`;

/**
 * A draft policy run against today's role holders and, when the org has
 * recorded decisions, the last `days` of them (#2126): who it would deny,
 * who it cannot decide for (a condition the check cannot answer denies),
 * and which recorded decisions would flip. Needs `org.manage_members`.
 */
export const POLICY_SIMULATION = gql`
  query PolicySimulation($draft: AstroliftPolicyDraftInput!, $days: Int, $limit: Int) {
    astroliftPolicySimulation(draft: $draft, days: $days, limit: $limit) {
      ok
      errors
      sources
      actions
      holdersCount
      holdersDeniedCount
      holdersUnknownCount
      holders {
        user {
          id
          username
          email
        }
        memberId
        outcome
        denied
        unknown
        allowed
        scopeKind
        scopeGuid
        sourceScopeLabel
        detail
      }
      auditRecorded
      windowDays
      decisionsEvaluated
      decisionsDeniedCount
      decisionsUnknownCount
      decisions {
        id
        occurredAt
        action
        actorId
        actorDisplay
        outcome
        permissions
        detail
      }
      notes
    }
  }
`;

/** One role, the org's own or a system one, with what it was duplicated from. */
export const GET_ROLE = gql`
  query GetRole($id: GUID!) {
    astroliftRole(id: $id) {
      id
      slug
      name
      description
      scopeLevel
      permissions
      isSystem
      bindingsCount
      duplicatedFrom {
        id
        slug
        name
        isSystem
        permissions
        deleted
      }
    }
  }
`;

/** One policy of the active org; another org's reads as null. */
export const GET_POLICY = gql`
  query GetPolicy($id: GUID!) {
    astroliftPolicy(id: $id) {
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

/**
 * The org's IdP group to role mappings (#2157), numbered; `groupExternalId`
 * narrows to one group exactly. A mapping grants its role to everyone the
 * IdP puts in the group, as a group binding does.
 */
export const LIST_GROUP_ROLE_MAPPINGS_PAGE = gql`
  query ListGroupRoleMappingsPage(
    $search: String
    $groupExternalId: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftGroupRoleMappingsPage(
      search: $search
      groupExternalId: $groupExternalId
      page: $page
      pageSize: $pageSize
    ) {
      items {
        id
        groupExternalId
        role {
          id
          slug
          name
          scopeLevel
          permissions
          isSystem
        }
        scopeKind
        scopeGuid
        sourceScopeLabel
        memberCount
        createdAt
      }
      totalCount
      page
      pageSize
    }
  }
`;

/**
 * Everyone with access on one team, project, app or agent (#2157): user and
 * group bindings on it and on its ancestors (when they inherit), the org's
 * group mappings and, on an app or agent, team shares. One row per grant
 * with the id of the row that grants it. `scopeId` is the object's guid.
 */
export const ACCESS_ON = gql`
  query AccessOn(
    $scopeKind: String!
    $scopeId: String!
    $search: String
    $page: Int
    $pageSize: Int
  ) {
    astroliftAccessOn(
      scopeKind: $scopeKind
      scopeId: $scopeId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      items {
        principalKind
        source
        bindingId
        user {
          id
          username
          email
        }
        memberId
        groupExternalId
        groupMemberCount
        teamId
        teamSlug
        teamName
        role {
          id
          slug
          name
          description
          scopeLevel
          permissions
          isSystem
        }
        accessLevel
        shareId
        scopeKind
        scopeGuid
        sourceScopeLabel
        inherited
        inherits
        expiresAt
      }
      totalCount
      page
      pageSize
    }
  }
`;
