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

/** Existing authorized server paging/search, without raw model configuration. */
export const PLAYGROUND_ENDPOINTS_PAGE = gql`
  query PlaygroundEndpointsPage($search: String, $page: Int, $pageSize: Int) {
    astroliftModelEndpointsPage(search: $search, sort: "name", page: $page, pageSize: $pageSize) {
      items {
        id
        name
        variant
        registeredAppSlug
        environmentName
      }
      totalCount
      page
      pageSize
    }
  }
`;
