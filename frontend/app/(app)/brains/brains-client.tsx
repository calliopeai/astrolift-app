"use client";

import { BrainsScreen } from "@/components/screens/brains/BrainsScreen";
import { useBrains } from "@/components/screens/brains/use-brains";

export function BrainsClient() {
  return <BrainsScreen {...useBrains()} />;
}
