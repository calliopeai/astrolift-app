import { gql } from "@apollo/client";

export const GET_MY_PERMISSIONS = gql`
  query GetMyPermissions {
    astroliftMyPermissions
  }
`;
