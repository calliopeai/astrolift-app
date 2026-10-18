import { gql } from "@apollo/client";

export const MY_NOTIFICATION_QUIET_HOURS = gql`
  query MyNotificationQuietHours {
    astroliftMyNotificationQuietHours {
      enabled
      timezone
      startMinute
      endMinute
      active
    }
  }
`;
