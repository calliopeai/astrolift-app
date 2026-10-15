import { gql } from "@apollo/client";

export const CREATE_WORKFLOW_DELIVERY_CHANNEL = gql`
  mutation CreateWorkflowDeliveryChannel(
    $name: String!
    $kind: String!
    $credential: String
    $slackChannel: String
    $emailRecipients: [String!]
  ) {
    createWorkflowDeliveryChannel(
      name: $name
      kind: $kind
      credential: $credential
      slackChannel: $slackChannel
      emailRecipients: $emailRecipients
    ) {
      ok
      errors {
        field
        messages
      }
      channel {
        guid
        name
        kind
        slackChannel
        emailRecipients
        isActive
        hasCredential
      }
    }
  }
`;

export const UPDATE_WORKFLOW_DELIVERY_CHANNEL = gql`
  mutation UpdateWorkflowDeliveryChannel($channelId: GUID!, $isActive: Boolean) {
    updateWorkflowDeliveryChannel(channelId: $channelId, isActive: $isActive) {
      ok
      errors {
        field
        messages
      }
      channel {
        guid
        name
        kind
        slackChannel
        emailRecipients
        isActive
        hasCredential
      }
    }
  }
`;

export const DELETE_WORKFLOW_DELIVERY_CHANNEL = gql`
  mutation DeleteWorkflowDeliveryChannel($channelId: GUID!) {
    deleteWorkflowDeliveryChannel(channelId: $channelId) {
      ok
      errors {
        field
        messages
      }
    }
  }
`;

export const SET_WORKFLOW_DELIVERY_CHANNELS = gql`
  mutation SetWorkflowDeliveryChannels($workflowId: GUID!, $channelIds: [GUID!]!) {
    setWorkflowDeliveryChannels(workflowId: $workflowId, channelIds: $channelIds) {
      ok
      errors {
        field
        messages
      }
      selection {
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
  }
`;
