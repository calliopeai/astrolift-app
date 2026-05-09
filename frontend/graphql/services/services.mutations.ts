import { gql } from "@apollo/client";

export const SET_APP_SECRET = gql`
  mutation SetAppSecret($input: SetAppSecretInput!) {
    setAppSecret(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        appSlug
        key
        rawManifestStaged
      }
    }
  }
`;

export const DELETE_APP_SECRET = gql`
  mutation DeleteAppSecret($input: DeleteAppSecretInput!) {
    deleteAppSecret(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        appSlug
        key
        rawManifestStaged
      }
    }
  }
`;

export const BULK_IMPORT_APP_SECRETS = gql`
  mutation BulkImportAppSecrets($input: BulkImportAppSecretsInput!) {
    bulkImportAppSecrets(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        appSlug
        keysSet
        rawManifestStaged
      }
    }
  }
`;

export const ATTACH_SECRET_BUNDLE = gql`
  mutation AttachSecretBundle($input: AttachSecretBundleInput!) {
    attachSecretBundle(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        bundleSlug
        bundleName
        environmentName
        prefix
      }
    }
  }
`;

export const DETACH_SECRET_BUNDLE = gql`
  mutation DetachSecretBundle($input: DetachSecretBundleInput!) {
    detachSecretBundle(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        attachmentId
      }
    }
  }
`;

const MANAGED_SERVICE_FIELDS = `
  id
  name
  kind
  variant
  status
  config
  registeredAppSlug
  environmentName
  createdAt
  updatedAt
`;

export const PROVISION_MANAGED_SERVICE = gql`
  mutation ProvisionManagedService($input: ProvisionManagedServiceInput!) {
    provisionManagedService(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        ${MANAGED_SERVICE_FIELDS}
      }
    }
  }
`;

export const UPDATE_MANAGED_SERVICE = gql`
  mutation UpdateManagedService($input: UpdateManagedServiceInput!) {
    updateManagedService(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        ${MANAGED_SERVICE_FIELDS}
      }
    }
  }
`;

export const DEPROVISION_MANAGED_SERVICE = gql`
  mutation DeprovisionManagedService($input: DeprovisionManagedServiceInput!) {
    deprovisionManagedService(input: $input) {
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
