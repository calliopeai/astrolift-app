import { gql } from "@apollo/client";

export const MY_NOTIFICATION_INBOX_STREAM = gql`
  subscription MyNotificationInboxStream($after: String) {
    astroliftMyNotificationStream(after: $after) {
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
