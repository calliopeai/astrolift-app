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
