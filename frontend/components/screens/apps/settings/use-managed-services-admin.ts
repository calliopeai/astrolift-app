"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  REPROVISION_MANAGED_SERVICE,
  UPDATE_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import type { AstroliftManagedService } from "@/graphql/services/services.types";
import { usePendingActions } from "@/hooks/use-pending-actions";

interface ManagedServicesListResp {
  astroliftManagedServices: AstroliftManagedService[];
}
interface ReprovisionResp {
  reprovisionManagedService: MutationResult<AstroliftManagedService>;
}
interface UpdateManagedServiceResp {
  updateManagedService: MutationResult<AstroliftManagedService>;
}

/** Text editing preserves unchanged JSON values and edits JSON-shaped values as raw text. */
export function configValueToString(v: unknown): string {
  return v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
}
export function configValue(target: AstroliftManagedService, field: string): unknown {
  return target.config && Object.hasOwn(target.config, field) ? target.config[field] : undefined;
}
/** The server's exact wildcard contract permits only existing config keys here. */
export function editableConfigKeys(target: AstroliftManagedService): string[] {
  const editable = target.editableFields ?? [];
  return editable.length === 1 && editable[0] === "*" ? Object.keys(target.config ?? {}) : editable;
}
/** Available read observations only: no server CAS or unqueried provider identity. */
export function managedServiceObservation(target: AstroliftManagedService): string {
  return JSON.stringify([
    target.id,
    target.registeredAppSlug,
    target.kind,
    target.variant,
    target.environmentName,
    target.createdAt,
    target.updatedAt,
    target.status,
    target.config,
    target.editableFields,
    target.appliedConfig,
    target.operationKind,
    target.operationWorkflowId,
    target.operationRunId,
    target.operationStartedAt,
    target.operationCompletedAt,
    target.lastActionAt,
    target.lastActionKind,
  ]);
}

/** Current-target inputs remain unchanged; an accepted request is separate from its follow-up read. */
export function useManagedServicesAdmin(appSlug: string) {
  const t = useTranslations("apps.managedServicesAdmin");
  const query = useQuery<ManagedServicesListResp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug, environmentName: null },
    fetchPolicy: "cache-and-network",
  });
  const observed = Array.isArray(query.data?.astroliftManagedServices);
  const services = (query.data?.astroliftManagedServices ?? []).filter(
    (s) => s.status !== "deleted"
  );
  const [target, setTarget] = React.useState({ slug: appSlug, epoch: 0 });
  if (target.slug !== appSlug) setTarget({ slug: appSlug, epoch: target.epoch + 1 });
  const epoch = target.epoch;
  const [readFailure, setReadFailure] = React.useState<{ epoch: number; message: string } | null>(
    null
  );
  if (query.error && (readFailure?.epoch !== epoch || readFailure.message !== query.error.message))
    setReadFailure({ epoch, message: query.error.message });
  else if (!query.loading && !query.error && observed && readFailure?.epoch === epoch)
    setReadFailure(null);
  const error =
    (query.error ? query.error.message || t("readFailed") : null) ??
    (readFailure?.epoch === epoch ? readFailure.message || t("readFailed") : null) ??
    (!query.loading && !observed ? t("readFailed") : null);
  const current = React.useRef<{
    epoch: number;
    services: AstroliftManagedService[];
    error: string | null;
  } | null>(null);
  React.useLayoutEffect(() => {
    current.current = { epoch, services, error };
    return () => {
      current.current = null;
    };
  }, [epoch, services, error]);
  const { pending, begin, finish } = usePendingActions();
  const reprovisionAction = JSON.stringify([epoch, "reprovision"]);
  const updateAction = JSON.stringify([epoch, "update"]);
  const [reprovision] = useMutation<ReprovisionResp>(REPROVISION_MANAGED_SERVICE);
  const [update] = useMutation<UpdateManagedServiceResp>(UPDATE_MANAGED_SERVICE);
  function reviewed(service: AstroliftManagedService) {
    const latest = current.current;
    return (
      latest?.epoch === epoch &&
      !latest.error &&
      latest.services.some(
        (s) =>
          s.id === service.id && managedServiceObservation(s) === managedServiceObservation(service)
      )
    );
  }
  function diagnostic(err: unknown, fallback: "reprovisionFailed" | "updateFailed") {
    return err instanceof Error && err.message
      ? err.message
      : typeof err === "string" && err
        ? err
        : t(fallback);
  }
  async function refresh() {
    if (current.current?.epoch !== epoch) return;
    try {
      await query.refetch({ appSlug, environmentName: null });
    } catch (err) {
      toast.warning(t("refreshWarning"), {
        description: err instanceof Error ? err.message : typeof err === "string" ? err : undefined,
      });
    }
  }
  async function onReprovision(service: AstroliftManagedService): Promise<boolean> {
    if (!reviewed(service) || !begin(reprovisionAction)) return false;
    try {
      let response;
      try {
        ({ data: response } = await reprovision({
          variables: { input: { managedServiceId: service.id } },
        }));
      } catch (err) {
        toast.error(diagnostic(err, "reprovisionFailed"));
        return false;
      }
      const result = response?.reprovisionManagedService;
      if (!result?.ok) {
        toast.error(result?.errors?.[0]?.message || t("reprovisionFailed"));
        return false;
      }
      toast.success(t("reprovisionAccepted", { name: service.name || service.kind }));
      await refresh();
      return true;
    } finally {
      finish(reprovisionAction);
    }
  }
  async function onSave(
    service: AstroliftManagedService,
    values: Record<string, string>
  ): Promise<boolean> {
    if (!reviewed(service)) return false;
    const changes: [string, unknown][] = [];
    for (const field of editableConfigKeys(service)) {
      const next = Object.hasOwn(values, field)
        ? values[field]
        : configValueToString(configValue(service, field));
      const original = configValue(service, field);
      if (next === configValueToString(original)) continue;
      let value: unknown = next;
      if (typeof original === "number") {
        if (!next.trim() || !Number.isFinite(Number(next))) {
          toast.error(t("invalidNumber", { field }));
          return false;
        }
        value = Number(next);
      } else if (typeof original === "boolean") {
        const text = next.trim();
        if (!["true", "false", "1", "0"].includes(text)) {
          toast.error(t("invalidBoolean", { field }));
          return false;
        }
        value = text === "true" || text === "1";
      }
      changes.push([field, value]);
    }
    if (!changes.length) {
      toast.message(t("noChanges"));
      return false;
    }
    if (!begin(updateAction)) return false;
    try {
      // The backend replaces config, so preserve every unchanged typed value from this review.
      const config = Object.fromEntries([...Object.entries(service.config ?? {}), ...changes]);
      let response;
      try {
        ({ data: response } = await update({ variables: { input: { id: service.id, config } } }));
      } catch (err) {
        toast.error(diagnostic(err, "updateFailed"));
        return false;
      }
      const result = response?.updateManagedService;
      if (!result?.ok) {
        toast.error(result?.errors?.[0]?.message || t("updateFailed"));
        return false;
      }
      toast.success(t("updateAccepted", { name: service.name || service.kind }));
      await refresh();
      return true;
    } finally {
      finish(updateAction);
    }
  }
  const reads: { target?: string; error?: string | null; onRetry?: () => void } = {
    target: appSlug,
    error,
    onRetry: () => void query.refetch({ appSlug, environmentName: null }).catch(() => {}),
  };
  return {
    services,
    loading: query.loading && !observed,
    reprovisioning: pending.has(reprovisionAction),
    updating: pending.has(updateAction),
    onReprovision,
    onSave,
    ...reads,
  };
}
