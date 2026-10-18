import { gql } from "@apollo/client";

export const MARK_AGENT_MESSAGES_READ = gql`
  mutation MarkAgentMessagesRead($ids: [GUID!], $threadId: GUID) {
    markAgentMessagesRead(ids: $ids, threadId: $threadId) {
      ok
      errors {
        code
        message
        field
      }
      data {
        marked
      }
    }
  }
`;

export const REPLY_TO_AGENT_MESSAGE = gql`
  mutation ReplyToAgentMessage($input: AstroliftReplyToAgentMessageInput!) {
    replyToAgentMessage(input: $input) {
      ok
      errors {
        code
        message
        field
      }
      data {
        id
        messageId
        authorLabel
        body
        choices
        decision
        route
        deliveredAt
        wakeTaskId
        createdAt
      }
    }
  }
`;
