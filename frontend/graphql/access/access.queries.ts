import { gql } from "@apollo/client";

/**
 * Permission analysis (core/schema/types/permission_analysis.py): the
 * resolver's own answer and trace, for Admin › Access › Check access.
 * Self or superuser only on the backend; compare is superuser only.
 */
export const PERMISSION_DIAGNOSE = gql`
  query PermissionDiagnose($userId: ID!, $permission: String!) {
    permissionDiagnose(userId: $userId, permission: $permission) {
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
  query PermissionCompare($userIdA: ID!, $userIdB: ID!) {
    permissionCompare(userIdA: $userIdA, userIdB: $userIdB) {
      userAUsername
      userBUsername
      onlyA
      onlyB
      shared
    }
  }
`;
