"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  REPROVISION_MANAGED_SERVICE,
  UPDATE_MANAGED_SERVICE,
} from "@/graphql/services/services.mutations";
import { LIST_MANAGED_SERVICES } from "@/graphql/services/services.queries";
import type { AstroliftManagedService } from "@/graphql/services/services.types";

interface ManagedServicesListResp {
  astroliftManagedServices: AstroliftManagedService[];
}

interface ReprovisionResp {
  reprovisionManagedService: MutationResult<AstroliftManagedService>;
}

interface UpdateManagedServiceResp {
  updateManagedService: MutationResult<AstroliftManagedService>;
}

/** A config value as the edit sheet's text input shows it. */
export function configValueToString(v: unknown): string {
  return v == null ? "" : typeof v === "string" ? v : JSON.stringify(v);
}

/**
 * The app's live managed services, plus in-place edit of hot-swappable
 * fields and full re-provision. Both mutations refetch the list.
 */
export function useManagedServicesAdmin(appSlug: string) {
  const { data, loading } = useQuery<ManagedServicesListResp>(LIST_MANAGED_SERVICES, {
    variables: { appSlug, environmentName: null },
    fetchPolicy: "cache-and-network",
  });
  const services = (data?.astroliftManagedServices ?? []).filter((s) => s.status !== "deleted");

  const refetchQueries = [
    { query: LIST_MANAGED_SERVICES, variables: { appSlug, environmentName: null } },
  ];
  const [reprovision, { loading: reprovisioning }] = useMutation<ReprovisionResp>(
    REPROVISION_MANAGED_SERVICE,
    { refetchQueries, awaitRefetchQueries: true }
  );
  const [update, { loading: updating }] = useMutation<UpdateManagedServiceResp>(
    UPDATE_MANAGED_SERVICE,
    { refetchQueries, awaitRefetchQueries: true }
  );

  /** Resolves true when the re-provision was accepted (close the dialog). */
  async function onReprovision(target: AstroliftManagedService): Promise<boolean> {
    try {
      const { data } = await reprovision({
        variables: { input: { managedServiceId: target.id } },
      });
      const env = data?.reprovisionManagedService;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Re-provision failed.");
        return false;
      }
      toast.success(`Re-provisioning ${target.name || target.kind}.`);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Re-provision failed.");
      return false;
    }
  }

  /**
   * Save the edited fields that differ from the service's config. Resolves
   * true when the update landed (close the sheet).
   */
  async function onSave(
    target: AstroliftManagedService,
    values: Record<string, string>
  ): Promise<boolean> {
    const editable = target.editableFields ?? [];
    const changes: Record<string, unknown> = {};
    for (const field of editable) {
      const next = values[field] ?? "";
      const original = target.config?.[field];
      const originalStr = configValueToString(original);
      if (next === originalStr) continue;
      // Preserve the original scalar type when round-tripping (number/bool stay
      // typed; strings + JSON-shaped values pass through as the raw input).
      if (typeof original === "number" && next.trim() !== "" && !isNaN(Number(next))) {
        changes[field] = Number(next);
      } else if (typeof original === "boolean") {
        changes[field] = next === "true" || next === "1";
      } else {
        changes[field] = next;
      }
    }
    if (Object.keys(changes).length === 0) {
      toast.message("No changes to save.");
      return false;
    }
    try {
      const { data } = await update({
        variables: { input: { id: target.id, config: changes } },
      });
      const env = data?.updateManagedService;
      if (!env?.ok) {
        toast.error(env?.errors?.[0]?.message ?? "Update failed.");
        return false;
      }
      toast.success(`Updated ${target.name || target.kind}.`);
      return true;
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Update failed.");
      return false;
    }
  }

  return {
    services,
    loading,
    reprovisioning,
    updating,
    onReprovision,
    onSave,
  };
}
