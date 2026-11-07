import { gql } from "@apollo/client";

export const NVKE_SITE_FIELDS = gql`
  fragment NvkeSiteFields on NvkeSiteType {
    id
    version
    name
    slug
    organizationNamespace
    isActive
    upstreamRevision
    createdAt
    updatedAt
    connections {
      id
      version
      surface
      endpoint
      isEnabled
      allowHttp
      timeoutSeconds
      hasCredentials
      verifiedAt
      verificationCode
    }
  }
`;

export const LIST_NVKE_SITES = gql`
  query ListNvkeSites($after: String, $limit: Int = 50) {
    astroliftNvkeSites(after: $after, limit: $limit) {
      items {
        ...NvkeSiteFields
      }
      nextCursor
      totalCount
    }
  }
  ${NVKE_SITE_FIELDS}
`;

export const GET_NVKE_SITE = gql`
  query GetNvkeSite($siteId: GUID!) {
    astroliftNvkeSite(siteId: $siteId) {
      ...NvkeSiteFields
    }
  }
  ${NVKE_SITE_FIELDS}
`;
