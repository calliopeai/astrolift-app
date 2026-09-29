"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { UPDATE_AGENT_ENVIRONMENT_SPEC } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_ENVIRONMENT_SPECS } from "@/graphql/agents/agents.queries";

interface UpdateEnvSpecResp {
  updateAgentEnvironmentSpec: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; slug: string; managedModel: boolean } | null;
  };
}

function firstError(errs: { message: string }[]): string {
  return errs[0]?.message ?? "unknown error";
}

/**
 * The spec-level "model source" toggle (#1173): optimistic local state and
 * the `UPDATE_AGENT_ENVIRONMENT_SPEC` write (refetching
 * `LIST_AGENT_ENVIRONMENT_SPECS` so every surface reflecting `managedModel`
 * re-reads). The data half of ManagedModelSectionView.
 */
export function useManagedModel(envSpecSlug: string, managedModel: boolean) {
  // Optimistic local state, reseeded from the loaded spec (and after a write
  // refetch) so it reflects the persisted value.
  const [managedOn, setManagedOn] = React.useState<boolean>(managedModel);
  const [managedBusy, setManagedBusy] = React.useState<boolean>(false);
  // Resync the optimistic local value when the loaded spec's stored value
  // changes (a different spec, or a refetch after a write). React's "adjust
  // state during render" pattern — avoids a setState-in-effect cascade.
  const [loadedManaged, setLoadedManaged] = React.useState<boolean>(managedModel);
  if (managedModel !== loadedManaged) {
    setLoadedManaged(managedModel);
    setManagedOn(managedModel);
  }

  const [updateSpec] = useMutation<UpdateEnvSpecResp>(UPDATE_AGENT_ENVIRONMENT_SPEC, {
    refetchQueries: [LIST_AGENT_ENVIRONMENT_SPECS],
  });

  async function onToggleManagedModel() {
    const next = !managedOn;
    setManagedOn(next); // optimistic
    setManagedBusy(true);
    try {
      const res = await updateSpec({
        variables: { slug: envSpecSlug, input: { managedModel: next } },
      });
      const payload = res.data?.updateAgentEnvironmentSpec;
      if (!payload?.ok) {
        setManagedOn(!next);
        toast.error(`Couldn't update model source: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      toast.success(next ? "Using cluster-native model" : "Using API key for model access");
    } catch (err) {
      setManagedOn(!next);
      toast.error(
        `Couldn't update model source: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setManagedBusy(false);
    }
  }

  return { managedOn, managedBusy, onToggleManagedModel };
}
