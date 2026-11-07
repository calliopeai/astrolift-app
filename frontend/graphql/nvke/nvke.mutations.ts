import { gql } from "@apollo/client";
import { NVKE_SITE_FIELDS } from "./nvke.queries";

export const CREATE_NVKE_SITE = gql`
  mutation CreateNvkeSite($input: CreateNvkeSiteInput!) {
    createNvkeSite(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeSiteFields
      }
    }
  }
  ${NVKE_SITE_FIELDS}
`;

export const CONFIGURE_NVKE_CONNECTION = gql`
  mutation ConfigureNvkeConnection($input: ConfigureNvkeConnectionInput!) {
    configureNvkeConnection(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeSiteFields
      }
    }
  }
  ${NVKE_SITE_FIELDS}
`;

export const SET_NVKE_SITE_ENABLED = gql`
  mutation SetNvkeSiteEnabled($input: SetNvkeSiteEnabledInput!) {
    setNvkeSiteEnabled(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeSiteFields
      }
    }
  }
  ${NVKE_SITE_FIELDS}
`;

export const REMOVE_NVKE_SITE = gql`
  mutation RemoveNvkeSite($input: RemoveNvkeSiteInput!) {
    removeNvkeSite(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeSiteFields
      }
    }
  }
  ${NVKE_SITE_FIELDS}
`;
