"use client";

import { useMutation } from "@apollo/client/react";
import { toast } from "sonner";

import {
  ABORT_DEPLOYMENT,
  APPROVE_DEPLOYMENT,
  REDEPLOY_APP,
  ROLLBACK_DEPLOYMENT,
} from "@/graphql/lifecycle/lifecycle.mutations";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import { useMyPermissions } from "@/lib/permissions/use-my-permissions";

import { APP_DEPLOYMENTS_OPERATION } from "./app-deployments-query";

interface MutationResultLite<T> {
  ok: boolean;
  errors: { code: string; message: string }[];
  data: T | null;
}

/**
 * Refetch by operation name: the tab's page carries a view, chips, a search
 * and a cursor, so no literal variables object names the page on screen.
 * `ListDeployments` is the approval queue and the frame's latest deploy.
 */
const REFETCH = [APP_DEPLOYMENTS_OPERATION, "ListDeployments"];

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
 * The lifecycle actions on the Deployments tab's rows: one set of
 * mutations for the whole list, each taking the deployment it acts on.
 * Abort, redeploy and rollback throw on failure so ConfirmDialog holds open
 * and reports the reason; approve fires without a dialog, so it reports its
 * own failure as a toast.
 */
export function useDeploymentActions() {
  const { can } = useMyPermissions();

  const [approve, approveState] = useMutation<{
    approveDeployment: MutationResultLite<AstroliftDeployment>;
  }>(APPROVE_DEPLOYMENT, { refetchQueries: REFETCH });
  const [abort, abortState] = useMutation<{
    abortDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ABORT_DEPLOYMENT, { refetchQueries: REFETCH });
  const [rollback, rollbackState] = useMutation<{
    rollbackDeployment: MutationResultLite<AstroliftDeployment>;
  }>(ROLLBACK_DEPLOYMENT, { refetchQueries: REFETCH });
  const [redeploy, redeployState] = useMutation<{
    redeployApp: MutationResultLite<AstroliftDeployment>;
  }>(REDEPLOY_APP, { refetchQueries: REFETCH });

  const busy =
    approveState.loading || abortState.loading || rollbackState.loading || redeployState.loading;

  async function onApprove(d: AstroliftDeployment) {
    try {
      const { data } = await approve({ variables: { input: { id: d.id } } });
      reportResult("approveDeployment", data?.approveDeployment);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Approve failed");
    }
  }

  async function onAbort(d: AstroliftDeployment, reason: string) {
    const { data } = await abort({ variables: { input: { id: d.id, reason } } });
    reportResult("abortDeployment", data?.abortDeployment);
  }

  async function onRedeploy(d: AstroliftDeployment) {
    const { data } = await redeploy({ variables: { input: { id: d.id } } });
    reportResult("redeployApp", data?.redeployApp);
  }

  async function onRollback(d: AstroliftDeployment) {
    const { data } = await rollback({ variables: { input: { id: d.id } } });
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
