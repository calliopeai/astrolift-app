"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import { ABORT_DEPLOYMENT, APPROVE_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface DeploymentsResp {
  astroliftDeployments: AstroliftDeployment[];
}

interface MutResp {
  approveDeployment?: MutationResult<AstroliftDeployment>;
  abortDeployment?: MutationResult<AstroliftDeployment>;
}

/** The short image tag (or id) a pending deploy is named by in the queue. */
export function shortDeploymentTag(deployment: AstroliftDeployment): string {
  return (deployment.imageTag ?? deployment.id).slice(0, 10);
}

/**
 * Approval queue for deploys whose strategy requires sign-off before they
 * can roll out. The data half of PendingDeploymentsView. Approve and
 * reject throw on failure so ConfirmDialog holds open and reports the
 * reason; either way the queue refetches afterwards.
 */
export function usePendingDeployments(appSlug: string) {
  const { can } = useMyPermissions();
  const canApprove = can("app.approve_deploy");

  const { data, loading, refetch } = useQuery<DeploymentsResp>(LIST_DEPLOYMENTS, {
    variables: { appSlug, limit: 25 },
    fetchPolicy: "cache-and-network",
    pollInterval: 30_000,
  });

  const pending = (data?.astroliftDeployments ?? []).filter((d) => d.status === "pending_approval");

  const [approve] = useMutation<MutResp>(APPROVE_DEPLOYMENT);
  const [abort] = useMutation<MutResp>(ABORT_DEPLOYMENT);

  async function onApprove(deployment: AstroliftDeployment) {
    try {
      const { data } = await approve({
        variables: { input: { id: deployment.id } },
      });
      if (data?.approveDeployment?.ok) {
        toast.success(`Approved ${shortDeploymentTag(deployment)}.`);
      } else {
        throw new Error(data?.approveDeployment?.errors?.[0]?.message ?? "Approve failed.");
      }
    } finally {
      void refetch();
    }
  }

  async function onReject(deployment: AstroliftDeployment, reason: string) {
    try {
      const { data } = await abort({
        variables: { input: { id: deployment.id, reason } },
      });
      if (data?.abortDeployment?.ok) {
        toast.success("Deployment rejected.");
      } else {
        throw new Error(data?.abortDeployment?.errors?.[0]?.message ?? "Reject failed.");
      }
    } finally {
      void refetch();
    }
  }

  return { loading: loading && !data, pending, canApprove, onApprove, onReject };
}
