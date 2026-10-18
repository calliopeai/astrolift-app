import { gql } from "@apollo/client";

import { AGENT_MESSAGE_FIELDS } from "./agent-messages.queries";

/** Each new agent message for the signed-in person, once per connection (#2434). */
export const ON_AGENT_MESSAGE_RECEIVED = gql`
  subscription OnAgentMessageReceived {
    astroliftAgentMessageReceived {
      ...AgentMessageFields
    }
  }
  ${AGENT_MESSAGE_FIELDS}
`;
