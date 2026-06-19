"use client";

import { useLazyQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  FileXIcon,
  RefreshCwIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { SCAN_AGENT_MANIFESTS } from "@/graphql/agents/agents.queries";
import type { AstroliftScanAgentManifestsResult } from "@/graphql/agents/agents.types";
import { getActiveOrgGuid } from "@/lib/identity/active-org";
import { cn } from "@/lib/utils";

import type { WizardState } from "../wizard-client";

interface ScanResp {
  scanAgentManifests: AstroliftScanAgentManifestsResult;
}

type FetchState = "idle" | "scanning" | "found" | "empty" | "error";

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

/**
 * Net-new step (no register-app analog). Scans the picked repo for agent
 * manifests via `scanAgentManifests` and lists each discovered agent with a
 * checkbox.
 *
 * Multi-select semantics: `registerAgentRepo` has NO per-manifest filter — it
 * registers the WHOLE repo's agents (idempotent on manifest path). So the
 * checkboxes are a client-side preview/confirm of what WILL be registered, not
 * a server-side selection. Already-registered agents are shown checked +
 * disabled (a re-scan only adds newly-added agents). The step is valid once at
 * least one not-yet-registered agent has been discovered.
 */
export function DiscoveryStep({ state, setState, setValid }: Props) {
  const orgId = getActiveOrgGuid() ?? "";
  const [fetchState, setFetchState] = React.useState<FetchState>("idle");
  const [fetchError, setFetchError] = React.useState<string>("");
  const [scan] = useLazyQuery<ScanResp>(SCAN_AGENT_MANIFESTS, {
    fetchPolicy: "network-only",
  });

  const run = React.useCallback(
    async (opts?: { manual?: boolean }) => {
      if (!orgId || !state.sourceRepo) return;
      const manual = opts?.manual ?? false;
      setFetchState("scanning");
      setFetchError("");
      const { data, error } = await scan({
        variables: {
          orgId,
          sourceRepo: state.sourceRepo,
          sourceKind: state.sourceKind,
          ref: state.ref || state.defaultBranch || "main",
        },
      });
      if (error) {
        setFetchState("error");
        setFetchError(error.message);
        setState((s) => ({ ...s, scanned: false, discoveredAgents: [], scanError: error.message }));
        if (manual) toast.error(`Scan failed: ${error.message}`);
        return;
      }
      const result = data?.scanAgentManifests;
      if (!result) {
        setFetchState("error");
        setFetchError("no response from server");
        setState((s) => ({ ...s, scanned: false, discoveredAgents: [], scanError: "no response" }));
        if (manual) toast.error("Scan failed: no response from server.");
        return;
      }
      if (!result.ok) {
        const msg = result.error ?? "scan failed";
        setFetchState("error");
        setFetchError(msg);
        setState((s) => ({ ...s, scanned: false, discoveredAgents: [], scanError: msg }));
        if (manual) toast.error(`Scan failed: ${msg}`);
        return;
      }
      const agents = result.agents;
      // Default-select every not-yet-registered agent. Already-registered
      // agents are pre-checked + disabled (they'll be matched, not
      // duplicated). The selection is a client-side preview — see the
      // component docstring.
      setState((s) => ({
        ...s,
        scanned: true,
        scanError: null,
        discoveredAgents: agents,
        selectedManifestPaths: agents
          .filter((a) => !a.alreadyRegistered)
          .map((a) => a.manifestPath),
      }));
      if (agents.length === 0) {
        setFetchState("empty");
        if (manual) toast.message("No agent manifests found in this repo.");
        return;
      }
      setFetchState("found");
      if (manual) {
        const newCount = agents.filter((a) => !a.alreadyRegistered).length;
        toast.success(
          `Found ${agents.length} agent${agents.length === 1 ? "" : "s"} (${newCount} new).`
        );
      }
    },
    [orgId, state.sourceRepo, state.sourceKind, state.ref, state.defaultBranch, scan, setState]
  );

  // Auto-scan on first entry (or whenever the repo / ref changed upstream).
  const lastScanKey = React.useRef<string>("");
  React.useEffect(() => {
    const key = `${orgId}|${state.sourceRepo}|${state.sourceKind}|${state.ref}`;
    if (!orgId || !state.sourceRepo) return;
    if (state.scanned && lastScanKey.current === key) return;
    if (lastScanKey.current === key && fetchState !== "idle") return;
    lastScanKey.current = key;
    void run();
  }, [orgId, state.sourceRepo, state.sourceKind, state.ref, state.scanned, fetchState, run]);

  // Valid once we've discovered at least one not-yet-registered agent. (If
  // every discovered agent is already registered there's nothing to do — the
  // single-repo path with one new agent satisfies acceptance (4).)
  const newAgentCount = state.discoveredAgents.filter((a) => !a.alreadyRegistered).length;
  React.useEffect(() => {
    setValid(state.scanned && newAgentCount > 0);
  }, [state.scanned, newAgentCount, setValid]);

  function toggle(manifestPath: string) {
    setState((s) => {
      const has = s.selectedManifestPaths.includes(manifestPath);
      return {
        ...s,
        selectedManifestPaths: has
          ? s.selectedManifestPaths.filter((p) => p !== manifestPath)
          : [...s.selectedManifestPaths, manifestPath],
      };
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-2">
        <p className="text-muted-foreground text-sm">
          Scanning{" "}
          <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">{state.sourceRepo}</code>{" "}
          on{" "}
          <code className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
            {state.ref || state.defaultBranch}
          </code>{" "}
          for agent manifests.
        </p>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => void run({ manual: true })}
          disabled={fetchState === "scanning"}
          title="Re-scan the repo for agent manifests"
        >
          <RefreshCwIcon className={cn("size-3.5", fetchState === "scanning" && "animate-spin")} />
          {fetchState === "scanning" ? "Scanning…" : "Re-scan"}
        </Button>
      </div>

      <ScanBanner
        state={fetchState}
        error={fetchError}
        onRetry={() => void run({ manual: true })}
      />

      {fetchState === "scanning" && state.discoveredAgents.length === 0 ? (
        <div className="flex flex-col gap-2">
          <div className="bg-muted/40 h-14 animate-pulse rounded-md" />
          <div className="bg-muted/40 h-14 animate-pulse rounded-md" />
        </div>
      ) : state.discoveredAgents.length > 0 ? (
        <>
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-medium">
              {state.discoveredAgents.length} agent
              {state.discoveredAgents.length === 1 ? "" : "s"} discovered
            </h3>
            <span className="text-muted-foreground text-xs">
              {newAgentCount} new · {state.discoveredAgents.length - newAgentCount} already
              registered
            </span>
          </div>

          <ul className="flex flex-col divide-y rounded-md border">
            {state.discoveredAgents.map((a) => {
              const checked =
                a.alreadyRegistered || state.selectedManifestPaths.includes(a.manifestPath);
              return (
                <li
                  key={a.manifestPath}
                  className={cn("flex items-center gap-3 p-3", a.alreadyRegistered && "opacity-70")}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={a.alreadyRegistered}
                    onChange={() => toggle(a.manifestPath)}
                    aria-label={`Select ${a.name}`}
                  />
                  <div className="bg-primary/10 text-primary flex size-8 shrink-0 items-center justify-center rounded-md">
                    <BotIcon className="size-4" />
                  </div>
                  <div className="flex min-w-0 flex-1 flex-col">
                    <div className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-medium">{a.name}</span>
                      <Badge variant="outline" className="shrink-0 text-[10px]">
                        {a.workloadKind}
                      </Badge>
                    </div>
                    <span className="text-muted-foreground truncate font-mono text-xs">
                      {a.manifestPath} · {a.slug}
                    </span>
                  </div>
                  {a.alreadyRegistered && (
                    <Badge variant="secondary" className="shrink-0 text-[10px]">
                      Already registered
                    </Badge>
                  )}
                </li>
              );
            })}
          </ul>

          <p className="text-muted-foreground rounded-md border border-dashed p-3 text-xs">
            Registration adds <strong>every new agent in the repo</strong> — the backend registers
            per repo, not per manifest, so the checkboxes are a preview of what will be created.
            Already-registered agents are matched, not duplicated.
          </p>
        </>
      ) : null}
    </div>
  );
}

function ScanBanner({
  state,
  error,
  onRetry,
}: {
  state: FetchState;
  error: string;
  onRetry: () => void;
}) {
  if (state === "idle" || state === "scanning") return null;
  if (state === "found") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
        <CheckCircle2Icon className="size-4 text-emerald-600 dark:text-emerald-400" />
        <span className="flex-1">Scanned the repo for agent manifests.</span>
      </div>
    );
  }
  if (state === "empty") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
        <FileXIcon className="size-4 text-amber-600 dark:text-amber-400" />
        <span className="flex-1">
          No agent manifests found. We looked for{" "}
          <code className="font-mono">agents/*/astrolift.toml</code> and a root{" "}
          <code className="font-mono">astrolift.toml</code> with an agent workload. Go back to pick
          a different repo or branch.
        </span>
      </div>
    );
  }
  return (
    <div className="border-destructive/40 bg-destructive/10 flex items-center gap-2 rounded-md border p-3 text-sm">
      <AlertTriangleIcon className="text-destructive size-4" />
      <span className="flex-1">Couldn&apos;t scan the repo — {error}</span>
      <Button type="button" size="sm" variant="outline" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
