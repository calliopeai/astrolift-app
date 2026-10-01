import { LONG, TOKEN_LONG, TOKENS } from "@/components/screens/teams/teams-tokens.fixtures";

import type { TokensScreenProps } from "./TokensScreen";

/** The API keys screen's props, less the list controller the story builds. */
export type TokensData = Omit<TokensScreenProps, "renderScopePicker" | "list">;

const noop = () => {};

export const TOKENS_SCREEN: TokensData = {
  rows: TOKENS,
  totalCount: TOKENS.length,
  nextCursor: null,
  loading: false,
  stale: false,
  error: null,
  onRetry: noop,
  creating: false,
  revoking: false,
  createdToken: null,
  onDismissCreated: noop,
  mcpEndpoint: "https://astrolift.example.com/api/mcp/v1/",
  onCreate: async () => true,
  onRevoke: async () => {},
  onCopyPlaintext: async () => {},
  onCopyMcpEndpoint: async () => {},
};

export const TOKENS_SCREEN_LONG: TokensData = {
  ...TOKENS_SCREEN,
  rows: [TOKEN_LONG, ...TOKENS],
  totalCount: 4,
  nextCursor: "cursor-2",
  createdToken: { apiToken: TOKEN_LONG, plaintext: `alft_at_${LONG}${LONG}` },
  mcpEndpoint: `https://${LONG}.example.com/api/mcp/v1/`,
};
