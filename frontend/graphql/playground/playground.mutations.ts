import { gql } from "@apollo/client";

/** Existing bounded in-cluster relay, with no browser URL or credentials. */
export const PLAYGROUND_PROMPT = gql`
  mutation PlaygroundPrompt($input: TestModelEndpointInput!) {
    testModelEndpoint(input: $input) {
      ok
      errors {
        code
        field
      }
      data {
        status
        reply
        latencyMs
        promptTokens
        completionTokens
        totalTokens
      }
    }
  }
`;
