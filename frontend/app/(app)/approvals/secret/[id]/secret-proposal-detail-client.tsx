"use client";

import { SecretProposalDetailScreen } from "@/components/screens/approvals/SecretProposalDetail";
import { useSecretProposalDetail } from "@/components/screens/approvals/use-secret-proposal-detail";

/** Secret-change proposal detail (#488): approve / reject / withdraw. */
export function SecretProposalDetailClient({ proposalId }: { proposalId: string }) {
  return <SecretProposalDetailScreen {...useSecretProposalDetail(proposalId)} />;
}
