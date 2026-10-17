import { gql } from "@apollo/client";

export const SET_MY_NOTIFICATION_QUIET_HOURS = gql`
  mutation SetMyNotificationQuietHours($input: SetNotificationQuietHoursInput!) {
    setNotificationQuietHours(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        enabled
        timezone
        startMinute
        endMinute
        active
      }
    }
  }
`;
