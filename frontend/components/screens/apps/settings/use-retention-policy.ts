"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import type { MutationResult } from "@/graphql/identity/identity.types";
import { SET_RETENTION_POLICY } from "@/graphql/registry/registry.mutations";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRetentionPolicy } from "@/graphql/registry/registry.types";

export const RETENTION_SIGNALS: { signal: string; label: string }[] = [
  { signal: "logs", label: "Logs" },
  { signal: "metrics", label: "Metrics" },
  { signal: "traces", label: "Traces" },
  { signal: "audit_events", label: "Audit Events" },
];

export const RETENTION_DAY_OPTIONS = [7, 14, 30, 60, 90, 180, 365] as const;
export const RETENTION_DEFAULT_DAYS = 30;

interface SetRetentionResp {
  setRetentionPolicy: MutationResult<AstroliftRetentionPolicy>;
}

/**
 * Per-signal observability retention. `saving` marks the signals with a
 * save in flight. Refetches GET_APP so the app's policies re-render.
 */
export function useRetentionPolicy(appSlug: string) {
  const [saving, setSaving] = React.useState<Record<string, boolean>>({});

  const [setRetention] = useMutation<SetRetentionResp>(SET_RETENTION_POLICY, {
    refetchQueries: [{ query: GET_APP, variables: { slug: appSlug } }],
    awaitRefetchQueries: true,
  });

  async function onChange(signal: string, raw: string) {
    const n = parseInt(raw, 10);
    if (isNaN(n) || n < 1) return;
    setSaving((s) => ({ ...s, [signal]: true }));
    try {
      const { data } = await setRetention({
        variables: { input: { appSlug, signal, retentionDays: n } },
      });
      const env = data?.setRetentionPolicy;
      if (!env) {
        toast.error("No response from server.");
        return;
      }
      if (!env.ok) {
        toast.error(env.errors?.[0]?.message ?? "Failed to save retention policy.");
        return;
      }
      toast.success(
        `${RETENTION_SIGNALS.find((s) => s.signal === signal)?.label ?? signal} retention set to ${n} days.`
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save retention policy.");
    } finally {
      setSaving((s) => ({ ...s, [signal]: false }));
    }
  }

  return { saving, onChange };
}
