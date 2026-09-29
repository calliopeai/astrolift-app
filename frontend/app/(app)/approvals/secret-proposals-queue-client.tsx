"use client";

import { SecretProposalsQueue } from "@/components/screens/approvals/SecretProposalsQueue";
import { useSecretProposalsQueue } from "@/components/screens/approvals/use-secret-proposals-queue";

/** Secret-change proposals queue (#488) on the global /approvals page. */
export function SecretProposalsQueueClient() {
  return <SecretProposalsQueue {...useSecretProposalsQueue()} />;
}
