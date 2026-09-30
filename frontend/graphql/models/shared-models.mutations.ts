import { gql } from "@apollo/client";
import { CLUSTER_MODEL_FIELDS } from "./shared-models.queries";

export const MODEL_MUTATION_ERROR_FIELDS = gql`
  fragment ModelMutationErrorFields on MutationError {
    code
    field
    message
    currentVersion
    requestedVersion
    requiresAttestation
    supportedMethods
  }
`;
export const MODEL_SUBSCRIPTION_FIELDS = gql`
  fragment ModelSubscriptionFields on ModelSubscription {
    id
    version
    modelDeploymentId
    appId
    appSlug
    appName
    environmentId
    environmentName
    alias
    bindingPrefix
    status
    canRevoke
    desiredEnabled
    desiredRevision
    appliedRevision
    reason
    reconcileStartedAt
    reconciledAt
  }
`;
export const PROVISION_CLUSTER_MODEL = gql`
  mutation ProvisionClusterModel($input: ProvisionClusterModelInput!) {
    provisionClusterModel(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        ...ClusterModelFields
      }
    }
  }
  ${MODEL_MUTATION_ERROR_FIELDS}
  ${CLUSTER_MODEL_FIELDS}
`;
export const SUBSCRIBE_CLUSTER_MODEL = gql`
  mutation SubscribeClusterModel($input: SubscribeClusterModelInput!) {
    subscribeClusterModel(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        restartRequired
        deployment {
          ...ClusterModelFields
        }
        subscription {
          ...ModelSubscriptionFields
        }
      }
    }
  }
  ${MODEL_MUTATION_ERROR_FIELDS}
  ${CLUSTER_MODEL_FIELDS}
  ${MODEL_SUBSCRIPTION_FIELDS}
`;
export const REVOKE_MODEL_SUBSCRIPTION = gql`
  mutation RevokeModelSubscription($input: RevokeModelSubscriptionInput!) {
    revokeModelSubscription(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        restartRequired
        deployment {
          ...ClusterModelFields
        }
        subscription {
          ...ModelSubscriptionFields
        }
      }
    }
  }
  ${MODEL_MUTATION_ERROR_FIELDS}
  ${CLUSTER_MODEL_FIELDS}
  ${MODEL_SUBSCRIPTION_FIELDS}
`;
export const UPDATE_CLUSTER_MODEL = gql`
  mutation UpdateClusterModel($input: UpdateClusterModelInput!) {
    updateClusterModel(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        ...ClusterModelFields
      }
    }
  }
  ${MODEL_MUTATION_ERROR_FIELDS}
  ${CLUSTER_MODEL_FIELDS}
`;
export const DEPROVISION_CLUSTER_MODEL = gql`
  mutation DeprovisionClusterModel($input: DeprovisionClusterModelInput!) {
    deprovisionClusterModel(input: $input) {
      ok
      errors {
        ...ModelMutationErrorFields
      }
      data {
        ...ClusterModelFields
      }
    }
  }
  ${MODEL_MUTATION_ERROR_FIELDS}
  ${CLUSTER_MODEL_FIELDS}
`;
