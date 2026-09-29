"use client";

import { TokenDetailScreen } from "@/components/screens/tokens/TokenDetailScreen";
import { useTokenDetail } from "@/components/screens/tokens/use-token-detail";

/** API token detail (#1106). */
export function TokenDetailClient({ id }: { id: string }) {
  return <TokenDetailScreen {...useTokenDetail(id)} />;
}
