import { gql } from "@apollo/client";

/** Selected persisted endpoint only; advisory, never invocation authorization. */
export const MODEL_PROMPT_READINESS = gql`
  query ModelPromptReadiness($id: GUID!) {
    astroliftModelPromptReadiness(id: $id) {
      state
      eligible
      maxPromptChars
      maxOutputTokens
      promptsPerMinute
      maxWaitSeconds
    }
  }
`;
