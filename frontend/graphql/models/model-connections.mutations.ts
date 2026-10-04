import { gql } from "@apollo/client";
import {
  MODEL_CONNECTION_POLICY_FIELDS,
  MODEL_CONNECTION_REQUEST_FIELDS,
} from "./model-connections.queries";

export const REQUEST_MODEL_CONNECTION = gql`
  mutation RequestModelConnection($input: RequestModelConnectionInput!) {
    requestModelConnection(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionRequestFields
      }
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
export const APPROVE_MODEL_CONNECTION_REQUEST = gql`
  mutation ApproveModelConnectionRequest($input: DecideModelConnectionRequestInput!) {
    approveModelConnectionRequest(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionRequestFields
      }
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
export const REJECT_MODEL_CONNECTION_REQUEST = gql`
  mutation RejectModelConnectionRequest($input: DecideModelConnectionRequestInput!) {
    rejectModelConnectionRequest(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionRequestFields
      }
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
export const CANCEL_MODEL_CONNECTION_REQUEST = gql`
  mutation CancelModelConnectionRequest($input: DecideModelConnectionRequestInput!) {
    cancelModelConnectionRequest(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionRequestFields
      }
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
export const FINALIZE_MODEL_CONNECTION_REQUEST = gql`
  mutation FinalizeModelConnectionRequest($input: DecideModelConnectionRequestInput!) {
    finalizeModelConnectionRequest(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionRequestFields
      }
    }
  }
  ${MODEL_CONNECTION_REQUEST_FIELDS}
`;
export const UPDATE_ORGANIZATION_MODEL_CONNECTION_POLICY = gql`
  mutation UpdateOrganizationModelConnectionPolicy(
    $input: UpdateOrganizationModelConnectionPolicyInput!
  ) {
    updateOrganizationModelConnectionPolicy(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionPolicyFields
      }
    }
  }
  ${MODEL_CONNECTION_POLICY_FIELDS}
`;
export const SET_MODEL_CONNECTION_RESTRICTION = gql`
  mutation SetModelConnectionRestriction($input: SetModelConnectionRestrictionInput!) {
    setModelConnectionRestriction(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        ...ModelConnectionPolicyFields
      }
    }
  }
  ${MODEL_CONNECTION_POLICY_FIELDS}
`;
