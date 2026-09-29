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
    data: { id: string; slug: string; vncEnabled: boolean } | null;
  };
}

function firstError(errs: { message: string }[]): string {
  return errs[0]?.message ?? "unknown error";
}

/**
 * The spec-level "live session" toggle: optimistic local state and the
 * `UPDATE_AGENT_ENVIRONMENT_SPEC` write (refetching
 * `LIST_AGENT_ENVIRONMENT_SPECS` so every surface reflecting `vncEnabled`
 * re-reads). The data half of VncSessionSectionView.
 */
export function useVncSession(envSpecSlug: string, vncEnabled: boolean) {
  // Optimistic local state, reseeded from the loaded spec (and after a write
  // refetch) so it reflects the persisted value.
  const [vncOn, setVncOn] = React.useState<boolean>(vncEnabled);
  const [vncBusy, setVncBusy] = React.useState<boolean>(false);
  // Resync the optimistic local value when the loaded spec's stored value
  // changes (a different spec, or a refetch after a write). React's "adjust
  // state during render" pattern — avoids a setState-in-effect cascade.
  const [loadedVnc, setLoadedVnc] = React.useState<boolean>(vncEnabled);
  if (vncEnabled !== loadedVnc) {
    setLoadedVnc(vncEnabled);
    setVncOn(vncEnabled);
  }

  const [updateSpec] = useMutation<UpdateEnvSpecResp>(UPDATE_AGENT_ENVIRONMENT_SPEC, {
    refetchQueries: [LIST_AGENT_ENVIRONMENT_SPECS],
  });

  async function onToggleVnc() {
    const next = !vncOn;
    setVncOn(next); // optimistic
    setVncBusy(true);
    try {
      const res = await updateSpec({
        variables: { slug: envSpecSlug, input: { vncEnabled: next } },
      });
      const payload = res.data?.updateAgentEnvironmentSpec;
      if (!payload?.ok) {
        setVncOn(!next);
        toast.error(`Couldn't update live session: ${firstError(payload?.errors ?? [])}`);
        return;
      }
      toast.success(next ? "Watchable VNC on" : "Watchable VNC off");
    } catch (err) {
      setVncOn(!next);
      toast.error(
        `Couldn't update live session: ${err instanceof Error ? err.message : String(err)}`
      );
    } finally {
      setVncBusy(false);
    }
  }

  return { vncOn, vncBusy, onToggleVnc };
}
