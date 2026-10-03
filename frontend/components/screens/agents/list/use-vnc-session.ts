"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";
import { useTranslations } from "next-intl";

import { UPDATE_AGENT_ENVIRONMENT_SPEC } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_ENVIRONMENT_SPECS } from "@/graphql/agents/agents.queries";

interface UpdateEnvSpecResp {
  updateAgentEnvironmentSpec: {
    ok: boolean;
    errors: { code: string; message: string; field: string | null }[];
    data: { id: string; slug: string; vncEnabled: boolean } | null;
  };
}

/**
 * The spec-level "live session" toggle: optimistic local state and the
 * `UPDATE_AGENT_ENVIRONMENT_SPEC` write (refetching
 * `LIST_AGENT_ENVIRONMENT_SPECS` so every surface reflecting `vncEnabled`
 * re-reads). The data half of VncSessionSectionView.
 */
export function useVncSession(envSpecSlug: string, vncEnabled: boolean, envSpecId?: string) {
  const t = useTranslations("agentModelAccess");
  const scopeKey = JSON.stringify([envSpecId ?? null, envSpecSlug]);
  const scopeRef = React.useRef({
    key: scopeKey,
    slug: envSpecSlug,
    active: false,
    pending: false,
    stored: vncEnabled,
  });
  React.useLayoutEffect(() => {
    const scope = {
      key: scopeKey,
      slug: envSpecSlug,
      active: true,
      pending: false,
      stored: scopeRef.current.stored,
    };
    scopeRef.current = scope;
    return () => {
      scope.active = false;
    };
  }, [scopeKey, envSpecSlug]);
  React.useLayoutEffect(() => {
    if (scopeRef.current.key === scopeKey) scopeRef.current.stored = vncEnabled;
  }, [scopeKey, vncEnabled]);
  // Optimistic local state, reseeded from the loaded spec (and after a write
  // refetch) so it reflects the persisted value.
  const [vncOn, setVncOn] = React.useState<boolean>(vncEnabled);
  const [vncBusy, setVncBusy] = React.useState<boolean>(false);
  // Resync the optimistic local value when the loaded spec's stored value
  // changes (a different spec, or a refetch after a write). React's "adjust
  // state during render" pattern — avoids a setState-in-effect cascade.
  const [loadedVnc, setLoadedVnc] = React.useState<boolean>(vncEnabled);
  const [loadedSpec, setLoadedSpec] = React.useState(scopeKey);
  if (scopeKey !== loadedSpec || vncEnabled !== loadedVnc) {
    if (scopeKey !== loadedSpec) setVncBusy(false);
    setLoadedSpec(scopeKey);
    setLoadedVnc(vncEnabled);
    setVncOn(vncEnabled);
  }

  const [updateSpec] = useMutation<UpdateEnvSpecResp>(UPDATE_AGENT_ENVIRONMENT_SPEC, {
    fetchPolicy: "no-cache",
    refetchQueries: [LIST_AGENT_ENVIRONMENT_SPECS],
  });

  async function onToggleVnc() {
    const scope = scopeRef.current;
    if (!scope.active || scope.key !== scopeKey || scope.pending) return;
    scope.pending = true;
    const next = !vncOn;
    setVncOn(next); // optimistic
    setVncBusy(true);
    try {
      const res = await updateSpec({
        variables: { slug: envSpecSlug, input: { vncEnabled: next } },
      });
      if (!scope.active) return;
      const payload = res.data?.updateAgentEnvironmentSpec;
      if (!payload?.ok) {
        setVncOn(scope.stored);
        toast.error(
          t("vncUpdateFailed", { reason: payload?.errors?.[0]?.message ?? t("unknownError") })
        );
        return;
      }
      const acknowledged = payload.data;
      if (
        !acknowledged ||
        typeof acknowledged.id !== "string" ||
        !acknowledged.id.trim() ||
        (envSpecId !== undefined && acknowledged.id !== envSpecId) ||
        acknowledged.slug !== envSpecSlug ||
        acknowledged.vncEnabled !== next
      ) {
        setVncOn(scope.stored);
        toast.error(t("updateUnconfirmed"));
        return;
      }
      toast.success(t(next ? "vncSavedOn" : "vncSavedOff"));
    } catch (err) {
      if (!scope.active) return;
      setVncOn(scope.stored);
      toast.error(
        t("vncUpdateFailed", { reason: err instanceof Error ? err.message : String(err) })
      );
    } finally {
      scope.pending = false;
      if (scope.active) setVncBusy(false);
    }
  }

  return { vncOn, vncBusy, onToggleVnc };
}
