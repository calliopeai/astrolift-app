"use client";

import { useMutation } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { UPDATE_AGENT_ENVIRONMENT_SPEC } from "@/graphql/agents/agents.mutations";
import { LIST_AGENT_ENVIRONMENT_SPECS } from "@/graphql/agents/agents.queries";
import { cn } from "@/lib/utils";

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
 * Spec-level "model source" control (#1173). When ON, the agent's environment
 * spec runs on the cluster's cloud-native model provider (Bedrock / Vertex) via
 * workload identity instead of an `ANTHROPIC_API_KEY` secret.
 *
 * Self-contained: owns its optimistic toggle state and the
 * `UPDATE_AGENT_ENVIRONMENT_SPEC` write (refetching `LIST_AGENT_ENVIRONMENT_SPECS`
 * so every surface reflecting `managedModel` re-reads). Shared by the agent
 * secrets dialog and the agent settings route so the toggle looks and behaves
 * identically on both — a single source, not divergent copies.
 */
export function ManagedModelSection({
  envSpecSlug,
  managedModel,
}: {
  envSpecSlug: string;
  managedModel: boolean;
}) {
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

  return (
    <div className="flex items-start justify-between gap-4 rounded-md border p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">Use cluster-native model (Bedrock / Vertex)</p>
        <p className="text-muted-foreground text-xs">
          Run this agent on the cluster&rsquo;s cloud model provider via workload identity instead
          of an API key.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <ManagedModelToggle
          checked={managedOn}
          onChange={onToggleManagedModel}
          disabled={managedBusy}
          label={managedOn ? "Disable cluster-native model" : "Enable cluster-native model"}
        />
        <span
          className={cn(
            "text-xs font-medium",
            managedOn ? "text-foreground" : "text-muted-foreground"
          )}
        >
          {managedOn ? "On" : "Off"}
        </span>
      </div>
    </div>
  );
}

/**
 * Accessible on/off switch. The repo has no shadcn/radix Switch primitive, so
 * this is a small local toggle styled with the same Tailwind tokens the rest of
 * the surface uses (mirrors the cluster-settings AuthGateToggle).
 */
function ManagedModelToggle({
  checked,
  onChange,
  disabled,
  label,
}: {
  checked: boolean;
  onChange: () => void;
  disabled?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        "relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full transition-colors",
        "focus-visible:ring-ring focus-visible:ring-2 focus-visible:ring-offset-2 focus-visible:outline-none",
        "disabled:cursor-not-allowed disabled:opacity-50",
        checked ? "bg-primary" : "bg-muted-foreground/30"
      )}
    >
      <span
        className={cn(
          "inline-block size-4 rounded-full bg-white shadow transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5"
        )}
      />
    </button>
  );
}
