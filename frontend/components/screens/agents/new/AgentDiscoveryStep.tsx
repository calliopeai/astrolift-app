"use client";

import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  FileXIcon,
  RefreshCwIcon,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type {
  AgentDiscoveryFields,
  AgentScanFetchState,
  useAgentDiscoveryStep,
} from "./use-agent-discovery-step";

export type AgentDiscoveryStepViewProps = ReturnType<typeof useAgentDiscoveryStep> & {
  state: AgentDiscoveryFields;
};

/**
 * Register-agent-repo step 2 (no register-app analog): lists each agent
 * discovered in the picked repo with a checkbox. The checkboxes are a
 * client-side preview of what will be registered; see useAgentDiscoveryStep.
 */
export function AgentDiscoveryStepView({
  state,
  fetchState,
  fetchError,
  newAgentCount,
  toggle,
  rescan,
}: AgentDiscoveryStepViewProps) {
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
          onClick={rescan}
          disabled={fetchState === "scanning"}
          title="Re-scan the repo for agent manifests"
        >
          <RefreshCwIcon className={cn("size-3.5", fetchState === "scanning" && "animate-spin")} />
          {fetchState === "scanning" ? "Scanning…" : "Re-scan"}
        </Button>
      </div>

      <ScanBanner state={fetchState} error={fetchError} onRetry={rescan} />

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
                      <Badge variant="outline" className="text-2xs shrink-0">
                        {a.workloadKind}
                      </Badge>
                    </div>
                    <span className="text-muted-foreground truncate font-mono text-xs">
                      {a.manifestPath} · {a.slug}
                    </span>
                  </div>
                  {a.alreadyRegistered && (
                    <Badge variant="secondary" className="text-2xs shrink-0">
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
  state: AgentScanFetchState;
  error: string;
  onRetry: () => void;
}) {
  if (state === "idle" || state === "scanning") return null;
  if (state === "found") {
    return (
      <div className="border-success-border bg-success/10 flex items-center gap-2 rounded-md border p-3 text-sm">
        <CheckCircle2Icon className="text-success-fg size-4" />
        <span className="flex-1">Scanned the repo for agent manifests.</span>
      </div>
    );
  }
  if (state === "empty") {
    return (
      <div className="border-warning-border bg-warning/10 flex items-center gap-2 rounded-md border p-3 text-sm">
        <FileXIcon className="text-warning-fg size-4" />
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
