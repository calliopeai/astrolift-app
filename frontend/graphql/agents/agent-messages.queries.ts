import { gql } from "@apollo/client";

/** One agent message as the inbox shows it (#2434). */
export const AGENT_MESSAGE_FIELDS = gql`
  fragment AgentMessageFields on AstroliftAgentMessage {
    id
    threadId
    kind
    source
    title
    body
    options {
      id
      label
      description
    }
    attachments {
      kind
      ref
      label
    }
    agentSlug
    agentName
    taskId
    taskStatus
    replyToId
    requestSequence
    expiresAt
    createdAt
    deliveredAt
    readAt
    answerable
    replies {
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
`;

export const LIST_AGENT_MESSAGE_THREADS = gql`
  query ListAgentMessageThreads($unreadOnly: Boolean! = false, $after: String, $first: Int! = 25) {
    astroliftAgentMessageThreads(unreadOnly: $unreadOnly, after: $after, first: $first) {
      unreadCount
      nextCursor
      items {
        threadId
        agentSlug
        agentName
        taskId
        taskStatus
        lastMessageAt
        messageCount
        unreadCount
        lastMessage {
          ...AgentMessageFields
        }
      }
    }
  }
  ${AGENT_MESSAGE_FIELDS}
`;

export const LIST_AGENT_MESSAGES = gql`
  query ListAgentMessages(
    $threadId: GUID
    $unreadOnly: Boolean! = false
    $after: String
    $first: Int! = 50
  ) {
    astroliftAgentMessages(
      threadId: $threadId
      unreadOnly: $unreadOnly
      after: $after
      first: $first
    ) {
      unreadCount
      totalCount
      nextCursor
      items {
        ...AgentMessageFields
      }
    }
  }
  ${AGENT_MESSAGE_FIELDS}
`;

/** Only the unread count, for the shell's inbox button. */
export const AGENT_MESSAGES_UNREAD = gql`
  query AgentMessagesUnread {
    astroliftAgentMessages(unreadOnly: true, first: 1) {
      unreadCount
    }
  }
`;
