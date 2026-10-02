"use client";
import { SecretProposalsQueue } from "@/components/screens/approvals/SecretProposalsQueue";
import { useSecretProposalsQueue } from "@/components/screens/approvals/use-secret-proposals-queue";
export function SecretProposalsListClient() {
  return <SecretProposalsQueue {...useSecretProposalsQueue()} />;
}
