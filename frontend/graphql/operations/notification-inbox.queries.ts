import { gql } from "@apollo/client";

export const GET_MY_NOTIFICATION_INBOX = gql`
  query GetMyNotificationInbox($after: String, $limit: Int! = 50) {
    astroliftMyNotificationsPage(after: $after, limit: $limit) {
      items {
        id
        kind
        title
        body
        link
        readAt
        createdAt
        expiryWarning {
          appId
          appSlug
          expiresAt
          warningWindow
        }
      }
      endCursor
      hasMore
    }
  }
`;
