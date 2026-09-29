"use client";

import type * as React from "react";

import { ApprovalsQueueScreen } from "@/components/screens/approvals/ApprovalsQueue";
import { useApprovalsQueue } from "@/components/screens/approvals/use-approvals-queue";

/** Global approvals queue (#420). The screen owns the markup. */
export function ApprovalsQueueClient({ children }: { children?: React.ReactNode }) {
  return <ApprovalsQueueScreen {...useApprovalsQueue()}>{children}</ApprovalsQueueScreen>;
}
