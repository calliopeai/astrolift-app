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
          format
          createdAt
          failureCount
          secretRotatedAt
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
        field
      }
      data {
        id
        url
        events
        isActive
        format
        secretRotatedAt
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

export const TEST_FIRE_WEBHOOK = gql`
  mutation TestFireWebhook($input: TestWebhookInput!) {
    testWebhookSubscription(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        subscriptionId
        url
        delivered
        statusCode
        durationMs
        responseBodyExcerpt
        error
        deliveryId
        timestamp
      }
    }
  }
`;

export const ROTATE_OUTBOUND_WEBHOOK_SECRET = gql`
  mutation RotateOutboundWebhookSecret($input: RotateOutboundWebhookSecretInput!) {
    rotateOutboundWebhookSecret(input: $input) {
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
          format
          createdAt
          failureCount
          secretRotatedAt
        }
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

export const EXPORT_AUDIT_EVENTS = gql`
  mutation ExportAuditEvents($input: ExportAuditEventsInput!) {
    exportAuditEvents(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        format
        rowCount
        byteCount
        sha256
        downloadUrl
        expiresAt
        createdAt
      }
    }
  }
`;

export const EXPORT_ASTROLIFT_APP_LOGS = gql`
  mutation ExportAstroliftAppLogs($input: ExportAppLogsInput!) {
    exportAstroliftAppLogs(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        format
        status
        rowCount
        byteCount
        sha256
        truncated
        downloadUrl
        expiresAt
        createdAt
        errorMessage
      }
    }
  }
`;
