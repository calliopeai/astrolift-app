import { gql } from "@apollo/client";

export const TEMPORARY_APP_CAPABILITIES = gql`
  query TemporaryAppCapabilities {
    astroliftServerInfo {
      capabilities
    }
  }
`;

export const TEMPORARY_APP_POLICY = gql`
  query TemporaryAppPolicy {
    astroliftTemporaryAppPolicy {
      minTtlSeconds
      maxTtlSeconds
      temporaryOnlyForMembers
      ttlRequiredSurfaces
      dataGraceDays
    }
  }
`;

export const ENVIRONMENT_APP_EXPIRY = gql`
  query EnvironmentAppExpiry($slug: String!) {
    astroliftApp(slug: $slug) {
      id
      slug
      expiresAt
    }
  }
`;
