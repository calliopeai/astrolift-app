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
    data: { id: string; slug: string; vncEnabled: boolean } | null;
  };
}

function firstError(errs: { message: string }[]): string {
  return errs[0]?.message ?? "unknown error";
}

/**
 * Spec-level "live session" control. When ON, the agent's environment spec runs
 * on a watchable VNC image so operators can watch the live desktop session;
 * when OFF the run is headless and streams logs instead.
 *
 * Self-contained: owns its optimistic toggle state and the
 * `UPDATE_AGENT_ENVIRONMENT_SPEC` write (refetching `LIST_AGENT_ENVIRONMENT_SPECS`
 * so every surface reflecting `vncEnabled` re-reads). Shared by the agent
 * secrets dialog and the agent settings route so the toggle looks and behaves
 * identically on both — a single source, not divergent copies. Mirrors
 * `ManagedModelSection` exactly.
 */
export function VncSessionSection({
  envSpecSlug,
  vncEnabled,
}: {
  envSpecSlug: string;
  vncEnabled: boolean;
}) {
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

  return (
    <div className="flex items-start justify-between gap-4 rounded-md border p-3">
      <div className="space-y-1">
        <p className="text-sm font-medium">Live VNC session (watchable)</p>
        <p className="text-muted-foreground text-xs">
          Run this agent on a watchable VNC image so you can watch the live desktop session. Adds
          overhead — leave off for headless runs, which stream logs instead.
        </p>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <VncSessionToggle
          checked={vncOn}
          onChange={onToggleVnc}
          disabled={vncBusy}
          label={vncOn ? "Disable live VNC session" : "Enable live VNC session"}
        />
        <span
          className={cn("text-xs font-medium", vncOn ? "text-foreground" : "text-muted-foreground")}
        >
          {vncOn ? "On" : "Off"}
        </span>
      </div>
    </div>
  );
}

/**
 * Accessible on/off switch. The repo has no shadcn/radix Switch primitive, so
 * this is a small local toggle styled with the same Tailwind tokens the rest of
 * the surface uses (mirrors the managed-model ManagedModelToggle).
 */
function VncSessionToggle({
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
