"use client";

import { BrainsScreen } from "@/components/screens/brains/BrainsScreen";
import { useBrains } from "@/components/screens/brains/use-brains";

/** The list: one server page, each row with its cached status summary. */
export function BrainsClient() {
  return <BrainsScreen {...useBrains()} />;
}
