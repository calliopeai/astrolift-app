import { gql } from "@apollo/client";

export const GET_SHARED_MODEL_PROMPT_READINESS = gql`
  query GetSharedModelPromptReadiness(
    $id: GUID!
    $expectedClusterId: GUID!
    $expectedProviderId: GUID!
    $expectedVersion: Int!
  ) {
    astroliftSharedModelPromptReadiness(
      id: $id
      expectedClusterId: $expectedClusterId
      expectedProviderId: $expectedProviderId
      expectedVersion: $expectedVersion
    ) {
      state
      eligible
      maxPromptChars
      maxOutputTokens
      promptsPerMinute
      maxWaitSeconds
    }
  }
`;

export const TEST_SHARED_MODEL_ENDPOINT = gql`
  mutation TestSharedModelEndpoint($input: TestSharedModelEndpointInput!) {
    testSharedModelEndpoint(input: $input) {
      ok
      errors {
        code
        field
        message
        currentVersion
        requestedVersion
        requiresAttestation
        supportedMethods
      }
      data {
        status
        reply
        latencyMs
        promptTokens
        completionTokens
        totalTokens
        error
      }
    }
  }
`;
