import { gql } from "@apollo/client";

import { ZENTINELLE_CLUSTER_FIELDS, ZENTINELLE_CONNECTION_FIELDS } from "./zentinelle.queries";

// Every one of these is step-up gated server-side; selecting `code` and
// `supportedMethods` on errors lets the step-up link open the prompt.

export const CONNECT_ZENTINELLE = gql`
  mutation ConnectZentinelle($input: ConnectZentinelleInput!) {
    connectZentinelle(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleConnectionFields
      }
    }
  }
  ${ZENTINELLE_CONNECTION_FIELDS}
`;

export const SET_ZENTINELLE_GATEWAY_URL = gql`
  mutation SetZentinelleGatewayUrl($input: SetZentinelleGatewayUrlInput!) {
    setZentinelleGatewayUrl(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleConnectionFields
      }
    }
  }
  ${ZENTINELLE_CONNECTION_FIELDS}
`;

export const DISCONNECT_ZENTINELLE = gql`
  mutation DisconnectZentinelle($input: DisconnectZentinelleInput!) {
    disconnectZentinelle(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        warnings
        connection {
          ...ZentinelleConnectionFields
        }
      }
    }
  }
  ${ZENTINELLE_CONNECTION_FIELDS}
`;

export const REGISTER_ZENTINELLE_CLUSTER = gql`
  mutation RegisterZentinelleCluster($input: ZentinelleClusterInput!) {
    registerZentinelleCluster(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleClusterFields
      }
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;

export const SET_ZENTINELLE_GATEWAY_ENABLED = gql`
  mutation SetZentinelleGatewayEnabled($input: SetZentinelleGatewayEnabledInput!) {
    setZentinelleGatewayEnabled(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleClusterFields
      }
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;

export const ROTATE_ZENTINELLE_GATEWAY_CREDENTIAL = gql`
  mutation RotateZentinelleGatewayCredential($input: RotateZentinelleGatewayCredentialInput!) {
    rotateZentinelleGatewayCredential(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleClusterFields
      }
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;

export const UNREGISTER_ZENTINELLE_CLUSTER = gql`
  mutation UnregisterZentinelleCluster($input: ZentinelleClusterInput!) {
    unregisterZentinelleCluster(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleClusterFields
      }
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;

export const EXPORT_ZENTINELLE_AUDIT = gql`
  mutation ExportZentinelleAudit($input: ExportZentinelleAuditInput!) {
    exportZentinelleAudit(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        filename
        content
        contentType
        rowCount
      }
    }
  }
`;

/** A shared cluster, through the install connection (#1905). Platform operator only. */
export const REGISTER_ZENTINELLE_SHARED_CLUSTER = gql`
  mutation RegisterZentinelleSharedCluster($input: ZentinelleClusterInput!) {
    registerZentinelleSharedCluster(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleClusterFields
      }
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;

/** A shared cluster, through the install connection (#1905). Platform operator only. */
export const UNREGISTER_ZENTINELLE_SHARED_CLUSTER = gql`
  mutation UnregisterZentinelleSharedCluster($input: ZentinelleClusterInput!) {
    unregisterZentinelleSharedCluster(input: $input) {
      ok
      errors {
        code
        message
        field
        supportedMethods
      }
      data {
        ...ZentinelleClusterFields
      }
    }
  }
  ${ZENTINELLE_CLUSTER_FIELDS}
`;
