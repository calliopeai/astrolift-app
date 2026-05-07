import { gql } from "@apollo/client";

export const CREATE_WEBHOOK = gql`
  mutation CreateWebhook($input: CreateWebhookSubscriptionInput!) {
    createWebhookSubscription(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        plaintextSecret
        subscription {
          id
          url
          events
          isActive
          createdAt
          failureCount
        }
      }
    }
  }
`;

export const UPDATE_WEBHOOK = gql`
  mutation UpdateWebhook($input: UpdateWebhookSubscriptionInput!) {
    updateWebhookSubscription(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        url
        events
        isActive
      }
    }
  }
`;

export const DELETE_WEBHOOK = gql`
  mutation DeleteWebhook($input: DeleteWebhookSubscriptionInput!) {
    deleteWebhookSubscription(input: $input) {
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

export const MARK_ALL_NOTIFICATIONS_READ = gql`
  mutation MarkAllNotificationsRead {
    markAllNotificationsRead {
      ok
      errors {
        code
        message
      }
      data {
        marked
      }
    }
  }
`;

export const MARK_NOTIFICATION_READ = gql`
  mutation MarkNotificationRead($input: MarkNotificationReadInput!) {
    markNotificationRead(input: $input) {
      ok
      errors {
        code
        message
      }
      data {
        id
        readAt
      }
    }
  }
`;
