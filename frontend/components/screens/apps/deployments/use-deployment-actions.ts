"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

function reportResult(
  label: string,
  result: MutationResultLite<AstroliftDeployment> | null | undefined
) {
  if (!result) return;
  if (result.ok) {
    toast.success(`${label}: ${result.data?.status ?? "ok"}`);
  } else {
    throw new Error(result.errors[0]?.message ?? `${label} failed`);
  }
}

/**
 * One deployment row's lifecycle actions. The data half of
 * DeploymentRowActionsView. Abort, redeploy and rollback throw on failure
 * so ConfirmDialog holds open and reports the reason; approve reports its
 * own failure as a toast, since it fires without a dialog.
 */
export function useDeploymentActions(deployment: AstroliftDeployment, appSlug: string) {
  const { can } = useMyPermissions();
  const d = deployment;

  const refetch = [
    {
      query: LIST_DEPLOYMENTS,
      variables: { appSlug, limit: 100 },
    },
  ];

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { refetchQueries: refetch });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { refetchQueries: refetch });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { refetchQueries: refetch });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { refetchQueries: refetch });

  const busy =
    approveState.loading || abortState.loading || rollbackState.loading || redeployState.loading;

  async function onApprove() {
    try {
      const { data } = await approve({
        variables: { input: { id: d.id } },
      });
      reportResult("approveDeployment", data?.approveDeployment);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Approve failed");
    }
  }

  async function onAbort(reason: string) {
    const { data } = await abort({
      variables: { input: { id: d.id, reason } },
    });
    reportResult("abortDeployment", data?.abortDeployment);
  }

  async function onRedeploy() {
    const { data } = await redeploy({
      variables: { input: { id: d.id } },
    });
    reportResult("redeployApp", data?.redeployApp);
  }

  async function onRollback() {
    const { data } = await rollback({
      variables: { input: { id: d.id } },
    });
    reportResult("rollbackDeployment", data?.rollbackDeployment);
  }

  return {
    canApprove: can("app.approve_deploy"),
    canDeploy: can("app.deploy"),
    canRollback: can("app.rollback"),
    busy,
    onApprove,
    onAbort,
    onRedeploy,
    onRollback,
  };
}
