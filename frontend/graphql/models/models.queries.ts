import { gql } from "@apollo/client";

// Every model endpoint the caller can read, app- or project-owned (#2040).
export const LIST_MODEL_ENDPOINTS = gql`
  query ListModelEndpoints {
    astroliftModelEndpoints {
      id
      name
      variant
      status
      statusError
      config
      registeredAppSlug
      projectSlug
      ownerScope
      clusterSlug
      environmentName
    }
  }
`;
