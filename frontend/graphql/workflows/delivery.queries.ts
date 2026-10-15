import { gql } from "@apollo/client";

// Result delivery channels for scheduled workflows (#2244). A channel's
// credential is write-only: no query returns it, only `hasCredential`.

export const WORKFLOW_DELIVERY_CHANNELS = gql`
  query WorkflowDeliveryChannels {
    workflowDeliveryChannels {
      guid
      name
      kind
      slackChannel
      emailRecipients
      isActive
      hasCredential
    }
  }
`;

export const WORKFLOW_DELIVERY_SELECTION = gql`
  query WorkflowDeliverySelection($workflowId: GUID!) {
    workflowDeliverySelection(workflowId: $workflowId) {
      workflowGuid
      channelGuids
      recentDeliveries {
        guid
        channelGuid
        channelName
        purpose
        runOutcome
        status
        attempts
        lastError
        createdAt
        deliveredAt
      }
    }
  }
`;
