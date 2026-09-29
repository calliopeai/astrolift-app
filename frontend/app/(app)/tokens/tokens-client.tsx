"use client";

import { TokensScreen } from "@/components/screens/tokens/TokensScreen";
import { useTokens } from "@/components/screens/tokens/use-tokens";

import { ScopePicker } from "./scope-picker";

export function TokensClient() {
  return (
    <TokensScreen {...useTokens()} renderScopePicker={(props) => <ScopePicker {...props} />} />
  );
}
