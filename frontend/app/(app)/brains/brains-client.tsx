"use client";

import { BrainRowStatus } from "@/components/screens/brains/BrainRowStatus";
import { BrainsScreen } from "@/components/screens/brains/BrainsScreen";
import { useBrains } from "@/components/screens/brains/use-brains";

/** The list, each visible row reading its own status. */
export function BrainsClient() {
  return (
    <BrainsScreen
      {...useBrains()}
      statusCell={(row, part) => (
        <BrainRowStatus slug={row.slug} environments={row.environments} part={part} />
      )}
    />
  );
}
