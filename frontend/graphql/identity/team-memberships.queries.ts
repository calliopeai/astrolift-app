import { gql } from "@apollo/client";

export const TEAM_MEMBERSHIP_FIELDS = gql`
  fragment ReviewedTeamMembership on AstroliftTeamMembership {
    team {
      id
      version
      name
      slug
      canManageMembers
    }
    person {
      orgMemberId
      name
      email
      active
    }
    teamMemberId
    lifecycle
    canRemove
    sources {
      id
      source
      scopeKind
      roleName
      roleId
      sourceHref
      expiresAt
      expired
      removable
    }
  }
`;
export const GET_TEAM_ACCESS_NAVIGATION = gql`
  query GetTeamAccessNavigation {
    me {
      teamAccessNavigation {
        canViewTeams
        canManageTeamMembers
        canViewPeople
        canViewRoles
        canViewPolicies
        canCheckAccess
      }
    }
  }
`;
export const GET_MEMBERSHIP_TEAM = gql`
  query GetMembershipTeam($slug: String!) {
    astroliftTeamMembershipTeam(slug: $slug) {
      id
      version
      name
      slug
      canManageMembers
    }
  }
`;
export const GET_MEMBERSHIP_PERSON = gql`
  query GetMembershipPerson($orgMemberId: GUID!) {
    astroliftTeamMembershipPerson(orgMemberId: $orgMemberId) {
      orgMemberId
      name
      email
      active
    }
  }
`;
export const LIST_REVIEWED_TEAM_MEMBERSHIPS = gql`
  ${TEAM_MEMBERSHIP_FIELDS}
  query ListReviewedTeamMemberships($teamId: GUID!, $search: String, $page: Int!, $pageSize: Int!) {
    astroliftTeamMembershipsPage(
      teamId: $teamId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      totalCount
      page
      pageSize
      items {
        ...ReviewedTeamMembership
      }
    }
  }
`;
export const LIST_REVIEWED_PERSON_TEAMS = gql`
  ${TEAM_MEMBERSHIP_FIELDS}
  query ListReviewedPersonTeams(
    $orgMemberId: GUID!
    $search: String
    $page: Int!
    $pageSize: Int!
  ) {
    astroliftPersonTeamMembershipsPage(
      orgMemberId: $orgMemberId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      totalCount
      page
      pageSize
      items {
        ...ReviewedTeamMembership
      }
    }
  }
`;
export const LIST_TEAM_MEMBER_CANDIDATES = gql`
  query ListTeamMemberCandidates($teamId: GUID!, $search: String, $page: Int!, $pageSize: Int!) {
    astroliftTeamMemberCandidatesPage(
      teamId: $teamId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      totalCount
      page
      pageSize
      items {
        orgMemberId
        name
        email
        active
      }
    }
  }
`;
export const LIST_MEMBERSHIP_TEAMS = gql`
  query ListMembershipTeams($search: String, $page: Int!, $pageSize: Int!) {
    astroliftMembershipTeamsPage(search: $search, page: $page, pageSize: $pageSize) {
      totalCount
      page
      pageSize
      items {
        id
        version
        name
        slug
        canManageMembers
      }
    }
  }
`;
export const GET_TEAM_MEMBERSHIP_REVIEW = gql`
  ${TEAM_MEMBERSHIP_FIELDS}
  query GetTeamMembershipReview(
    $teamId: GUID!
    $orgMemberId: GUID!
    $kind: TeamMembershipChangeKind!
    $roleId: GUID
  ) {
    astroliftTeamMembershipReview(
      teamId: $teamId
      orgMemberId: $orgMemberId
      kind: $kind
      roleId: $roleId
    ) {
      kind
      expectedSource
      membership {
        ...ReviewedTeamMembership
      }
      roles {
        id
        version
        name
        permissions
      }
      remainingSources {
        id
        source
        scopeKind
        roleName
        roleId
        sourceHref
        expiresAt
        expired
        removable
      }
    }
  }
`;
export const CHANGE_TEAM_MEMBERSHIP = gql`
  mutation ChangeTeamMembership($input: ChangeAstroliftTeamMembershipInput!) {
    changeAstroliftTeamMembership(input: $input) {
      ok
      errors {
        code
        message
        requiresAttestation
        supportedMethods
      }
      data {
        requestId
        changeId
        committed
        replayed
        teamId
        orgMemberId
        teamMemberId
        removedBindingIds
        remainingSources {
          id
          source
          scopeKind
          roleName
          roleId
          sourceHref
          expiresAt
          expired
          removable
        }
      }
    }
  }
`;

export const LIST_TEAM_MEMBERSHIP_ROLES = gql`
  query ListTeamMembershipRoles($teamId: GUID!, $search: String, $page: Int!, $pageSize: Int!) {
    astroliftTeamMembershipRolesPage(
      teamId: $teamId
      search: $search
      page: $page
      pageSize: $pageSize
    ) {
      totalCount
      page
      pageSize
      items {
        id
        version
        name
        permissions
      }
    }
  }
`;
