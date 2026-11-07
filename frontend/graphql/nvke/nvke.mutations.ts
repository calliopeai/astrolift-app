import { gql } from "@apollo/client";
import { NVKE_OPERATION_FIELDS, NVKE_SITE_FIELDS } from "./nvke.queries";

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

export const REQUEST_NVKE_OPERATION = gql`
  mutation RequestNvkeOperation($input: RequestNvkeOperationInput!) {
    requestNvkeOperation(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const CANCEL_NVKE_OPERATION = gql`
  mutation CancelNvkeOperation($input: CancelNvkeOperationInput!) {
    cancelNvkeOperation(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const RESUME_NVKE_OPERATION = gql`
  mutation ResumeNvkeOperation($input: ResumeNvkeOperationInput!) {
    resumeNvkeOperation(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;

export const REQUEST_NVKE_OPERATION_TEXT = gql`
  mutation RequestNvkeOperationText($input: RequestNvkeOperationTextInput!) {
    requestNvkeOperationText(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ...NvkeOperationFields
      }
    }
  }
  ${NVKE_OPERATION_FIELDS}
`;
