"use client";

import { useMutation, useSubscription } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { ROLLBACK_DEPLOYMENT } from "@/graphql/lifecycle/lifecycle.mutations";
import { LIST_DEPLOYMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import { DEPLOYMENT_LIFECYCLE_STREAM } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftDeployment } from "@/graphql/lifecycle/lifecycle.types";
import type { MutationResult } from "@/graphql/identity/identity.types";

import { appDeploysVariables, useAppDeploys } from "../detail/use-app-deploys";

interface RollbackResp {
  rollbackDeployment: MutationResult<AstroliftDeployment>;
}

/**
 * The most-recent deployment, kept live by the lifecycle stream, and the
 * last-known-good it would roll back to. The data half of
 * DeploymentPanelView. It reads the deploys the app frame already holds
 * (use-app-deploys) and is the reader that keeps them live.
 */
export function useDeploymentPanel(appSlug: string) {
  const { deployments, loading, refetch } = useAppDeploys(appSlug, { live: true });

  // Refetch on any lifecycle event for this app — the row deltas come
  // through the same query so the rollback button stays accurate.
  useSubscription(DEPLOYMENT_LIFECYCLE_STREAM, {
    variables: { appSlug },
    onData: () => {
      void refetch();
    },
  });

  const current = deployments.at(0) ?? null;
  // For rollback, target the last `running` deploy that isn't the current
  // one — that's the version we'd actually flip traffic back to.
  const lastGood = React.useMemo(
    () => deployments.slice(1).find((d) => d.status === "running" || d.status === "rolled_back"),
    [deployments]
  );

  const [rollback, { loading: rolling }] = useMutation<RollbackResp>(ROLLBACK_DEPLOYMENT, {
    refetchQueries: [{ query: LIST_DEPLOYMENTS, variables: appDeploysVariables(appSlug) }],
    awaitRefetchQueries: true,
  });

  // Throws on a failed mutation so ConfirmDialog holds the dialog open and
  // reports the reason, rather than closing on a rollback that never ran.
  async function onRollback() {
    if (!lastGood) return;
    const { data } = await rollback({ variables: { input: { id: lastGood.id } } });
    if (data?.rollbackDeployment.ok) {
      toast.success("Rollback started.");
    } else {
      throw new Error(data?.rollbackDeployment.errors?.[0]?.message ?? "Rollback failed.");
    }
  }

  return {
    loading: loading && deployments.length === 0,
    current,
    lastGood,
    rolling,
    onRollback,
  };
}
