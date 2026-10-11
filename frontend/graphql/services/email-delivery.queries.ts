import { gql } from "@apollo/client";

const EMAIL_DELIVERY_TEST_FIELDS = gql`
  fragment EmailDeliveryTestFields on EmailDeliveryTest {
    id
    managedServiceId
    version
    requestId
    sender
    recipient
    status
    accountId
    region
    identity
    transport
    providerMessageId
    eventTrackingConfigured
    simulator
    createdAt
    acceptedAt
    observedAt
    reasonCode
  }
`;

export const EMAIL_DELIVERY_SUPPORT = gql`
  query EmailDeliverySupport($managedServiceId: GUID!) {
    emailDeliveryTestSupport(managedServiceId: $managedServiceId) {
      allowed
      reason
      serviceVersion
      sender
      identity
      accountId
      region
    }
  }
`;

export const EMAIL_DELIVERY_TESTS_PAGE = gql`
  ${EMAIL_DELIVERY_TEST_FIELDS}
  query EmailDeliveryTestsPage($managedServiceId: GUID!, $after: String, $limit: Int! = 25) {
    emailDeliveryTestsPage(managedServiceId: $managedServiceId, after: $after, limit: $limit) {
      items {
        ...EmailDeliveryTestFields
      }
      nextCursor
      totalCount
    }
  }
`;

export const SEND_EMAIL_DELIVERY_TEST = gql`
  ${EMAIL_DELIVERY_TEST_FIELDS}
  mutation SendEmailDeliveryTest($input: SendEmailDeliveryTestInput!) {
    sendEmailDeliveryTest(input: $input) {
      ok
      errors {
        code
        message
        field
        currentVersion
      }
      data {
        ...EmailDeliveryTestFields
      }
    }
  }
`;

export const EMAIL_DELIVERY_APPS = gql`
  query EmailDeliveryApps($search: String, $cursor: String, $limit: Int! = 25) {
    astroliftAppsPage(search: $search, cursor: $cursor, limit: $limit) {
      items {
        id
        name
        slug
        organizationSlug
        version
      }
      nextCursor
      totalCount
    }
  }
`;

export const EMAIL_DELIVERY_SERVICES = gql`
  query EmailDeliveryServices($appSlug: String!, $after: String, $limit: Int! = 25) {
    astroliftManagedServicesPage(appSlug: $appSlug, after: $after, limit: $limit) {
      items {
        id
        name
        kind
        variant
        status
        environmentName
        registeredAppSlug
      }
      nextCursor
      totalCount
    }
  }
`;

export const EMAIL_DELIVERY_APP = gql`
  query EmailDeliveryApp($slug: String!) {
    astroliftApp(slug: $slug) {
      id
      name
      slug
      organizationSlug
      version
    }
  }
`;
