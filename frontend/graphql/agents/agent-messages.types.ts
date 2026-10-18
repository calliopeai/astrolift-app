import type {
  AstroliftAgentMessage,
  AstroliftAgentMessageReply,
  AstroliftAgentMessageThread,
} from "@/graphql/__generated__/schema";

/** An agent message as the inbox reads it (#2434). */
export type AgentMessage = AstroliftAgentMessage;
export type AgentMessageReply = AstroliftAgentMessageReply;
export type AgentMessageThread = AstroliftAgentMessageThread;

/** What a person sends back: words, a choice, or an approval decision. */
export interface AgentMessageReplyDraft {
  body?: string;
  choices?: string[];
  decision?: "allow" | "deny";
}

export const AGENT_MESSAGE_KINDS = ["update", "question", "approval", "result", "alert"] as const;
export type AgentMessageKind = (typeof AGENT_MESSAGE_KINDS)[number];

export function isAgentMessageKind(value: string): value is AgentMessageKind {
  return (AGENT_MESSAGE_KINDS as readonly string[]).includes(value);
}
