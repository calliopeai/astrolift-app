"use client";
import { SecretProposalsSummary } from "@/components/screens/approvals/SecretProposalsSummary";
import { useSecretProposalsSummary } from "@/components/screens/approvals/use-secret-proposals-summary";
export function SecretProposalsQueueClient() {
  return <SecretProposalsSummary {...useSecretProposalsSummary()} />;
}
