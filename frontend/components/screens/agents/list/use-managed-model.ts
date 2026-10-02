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
    data: { id: string; slug: string; managedModel: boolean } | null;
  };
}

/**
 * The spec-level "model source" toggle (#1173): optimistic local state and
 * the `UPDATE_AGENT_ENVIRONMENT_SPEC` write (refetching
 * `LIST_AGENT_ENVIRONMENT_SPECS` so every surface reflecting `managedModel`
 * re-reads). The data half of ManagedModelSectionView.
 */
export function useManagedModel(envSpecSlug: string, managedModel: boolean, envSpecId?: string) {
  const t = useTranslations("agentModelAccess");
  const scopeKey = JSON.stringify([envSpecId ?? null, envSpecSlug]);
  const scopeRef = React.useRef({
    key: scopeKey,
    slug: envSpecSlug,
    active: false,
    pending: false,
    stored: managedModel,
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
    if (scopeRef.current.key === scopeKey) scopeRef.current.stored = managedModel;
  }, [scopeKey, managedModel]);
  // Optimistic local state, reseeded from the loaded spec (and after a write
  // refetch) so it reflects the persisted value.
  const [managedOn, setManagedOn] = React.useState<boolean>(managedModel);
  const [managedBusy, setManagedBusy] = React.useState<boolean>(false);
  // Resync the optimistic local value when the loaded spec's stored value
  // changes (a different spec, or a refetch after a write). React's "adjust
  // state during render" pattern — avoids a setState-in-effect cascade.
  const [loadedManaged, setLoadedManaged] = React.useState<boolean>(managedModel);
  const [loadedSpec, setLoadedSpec] = React.useState(scopeKey);
  if (scopeKey !== loadedSpec || managedModel !== loadedManaged) {
    if (scopeKey !== loadedSpec) setManagedBusy(false);
    setLoadedSpec(scopeKey);
    setLoadedManaged(managedModel);
    setManagedOn(managedModel);
  }

  const [updateSpec] = useMutation<UpdateEnvSpecResp>(UPDATE_AGENT_ENVIRONMENT_SPEC, {
    fetchPolicy: "no-cache",
    refetchQueries: [LIST_AGENT_ENVIRONMENT_SPECS],
  });

  async function onToggleManagedModel() {
    const scope = scopeRef.current;
    if (!scope.active || scope.key !== scopeKey || scope.pending) return;
    scope.pending = true;
    const next = !managedOn;
    setManagedOn(next); // optimistic
    setManagedBusy(true);
    try {
      const res = await updateSpec({
        variables: { slug: envSpecSlug, input: { managedModel: next } },
      });
      if (!scope.active) return;
      const payload = res.data?.updateAgentEnvironmentSpec;
      if (!payload?.ok) {
        setManagedOn(scope.stored);
        toast.error(
          t("modelUpdateFailed", { reason: payload?.errors?.[0]?.message ?? t("unknownError") })
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
        acknowledged.managedModel !== next
      ) {
        setManagedOn(scope.stored);
        toast.error(t("updateUnconfirmed"));
        return;
      }
      toast.success(t(next ? "modelSavedOn" : "modelSavedOff"));
    } catch (err) {
      if (!scope.active) return;
      setManagedOn(scope.stored);
      toast.error(
        t("modelUpdateFailed", { reason: err instanceof Error ? err.message : String(err) })
      );
    } finally {
      scope.pending = false;
      if (scope.active) setManagedBusy(false);
    }
  }

  return { managedOn, managedBusy, onToggleManagedModel };
}
