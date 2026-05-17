import { gql } from "@apollo/client";

export const LIST_APP_SECRETS = gql`
  query ListAppSecrets($appSlug: String!, $environmentName: String) {
    astroliftAppSecrets(appSlug: $appSlug, environmentName: $environmentName) {
      id
      key
      environmentName
      source
      bundleSlug
      managedServiceKind
      isMasked
      lastEditedAt
      lastEditedBy {
        id
        username
        displayName
      }
    }
  }
`;

export const LIST_APP_SECRET_BUNDLE_ATTACHMENTS = gql`
  query ListAppSecretBundleAttachments(
    $appSlug: String!
    $environmentName: String
  ) {
    astroliftAppSecretBundleAttachments(
      appSlug: $appSlug
      environmentName: $environmentName
    ) {
      id
      registeredAppSlug
      environmentName
      bundleSlug
      bundleName
      prefix
      teamSlug
      keyCount
      mergeOrder
      attachedAt
    }
  }
`;

export const LIST_SECRET_BUNDLES = gql`
  query ListSecretBundles {
    astroliftSecretBundles {
      id
      slug
      name
      backendRef
      createdAt
    }
  }
`;

export const LIST_MANAGED_SERVICES = gql`
  query ListManagedServices($appSlug: String!, $environmentName: String) {
    astroliftManagedServices(
      appSlug: $appSlug
      environmentName: $environmentName
    ) {
      id
      kind
      name
      environmentName
      registeredAppSlug
      status
      config
      createdAt
    }
  }
`;
