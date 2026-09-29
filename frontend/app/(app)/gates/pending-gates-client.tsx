"use client";

import { PendingGatesScreen } from "@/components/screens/approvals/PendingGates";
import { usePendingGates } from "@/components/screens/approvals/use-pending-gates";

/** Org-wide pending human gates (#1820). */
export function PendingGatesClient() {
  return <PendingGatesScreen {...usePendingGates()} />;
}
