import { gql } from "@apollo/client";

/**
 * Access writes the identity mutations file does not carry (#2157): IdP
 * group to role mappings, and changing a binding's role or expiry in place.
 */

const ERRORS = gql`
  fragment AccessMutationErrors on MutationError {
    code
    message
    field
  }
`;

export const CREATE_GROUP_ROLE_MAPPING = gql`
  ${ERRORS}
  mutation CreateGroupRoleMapping($input: CreateGroupRoleMappingInput!) {
    createGroupRoleMapping(input: $input) {
      ok
      errors {
        ...AccessMutationErrors
      }
      data {
        id
        groupExternalId
        role {
          id
          slug
          name
        }
        scopeKind
        scopeGuid
        sourceScopeLabel
        memberCount
        createdAt
      }
    }
  }
`;

export const DELETE_GROUP_ROLE_MAPPING = gql`
  ${ERRORS}
  mutation DeleteGroupRoleMapping($input: DeleteGroupRoleMappingInput!) {
    deleteGroupRoleMapping(input: $input) {
      ok
      errors {
        ...AccessMutationErrors
      }
    }
  }
`;

export const UPDATE_ROLE_BINDING = gql`
  ${ERRORS}
  mutation UpdateRoleBinding($input: UpdateRoleBindingInput!) {
    updateRoleBinding(input: $input) {
      ok
      errors {
        ...AccessMutationErrors
      }
      data {
        id
        role {
          id
          slug
          name
          scopeLevel
        }
        expiresAt
      }
    }
  }
`;
