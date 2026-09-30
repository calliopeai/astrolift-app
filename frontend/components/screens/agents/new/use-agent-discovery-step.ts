"use client";

import { useLazyQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { SCAN_AGENT_MANIFESTS } from "@/graphql/agents/agents.queries";
import type {
  AstroliftDiscoveredAgentManifest,
  AstroliftScanAgentManifestsResult,
} from "@/graphql/agents/agents.types";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";
import type { SourceKind } from "@/graphql/registry/registry.types";

interface ScanResp {
  scanAgentManifests: AstroliftScanAgentManifestsResult;
}

export type AgentScanFetchState = "idle" | "scanning" | "found" | "empty" | "error";

/** The slice of the register-agent-repo wizard state the discovery step reads and writes. */
export interface AgentDiscoveryFields {
  sourceKind: SourceKind;
  sourceRepo: string;
  defaultBranch: string;
  ref: string;
  scanned: boolean;
  scanError: string | null;
  discoveredAgents: AstroliftDiscoveredAgentManifest[];
  selectedManifestPaths: string[];
}

/**
 * Scans the picked repo for agent manifests via `scanAgentManifests`, writes
 * the result into the wizard state, auto-scans on entry, and reports
 * validity. The data half of AgentDiscoveryStepView.
 *
 * Multi-select semantics: `registerAgentRepo` has NO per-manifest filter — it
 * registers the WHOLE repo's agents (idempotent on manifest path). So the
 * checkboxes are a client-side preview/confirm of what WILL be registered, not
 * a server-side selection. Already-registered agents are shown checked +
 * disabled (a re-scan only adds newly-added agents). The step is valid once at
 * least one not-yet-registered agent has been discovered.
 */
export function useAgentDiscoveryStep<S extends AgentDiscoveryFields>(
  state: S,
  setState: React.Dispatch<React.SetStateAction<S>>,
  setValid: (valid: boolean) => void
) {
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const [fetchState, setFetchState] = React.useState<AgentScanFetchState>("idle");
  const [fetchError, setFetchError] = React.useState<string>("");
  const [scan] = useLazyQuery<ScanResp>(SCAN_AGENT_MANIFESTS, {
    fetchPolicy: "network-only",
  });

  const requestVersion = React.useRef(0);
  const scanKey = JSON.stringify([
    orgId,
    state.sourceRepo,
    state.sourceKind,
    state.ref || state.defaultBranch || "main",
  ]);
  React.useEffect(
    () => () => {
      requestVersion.current += 1;
    },
    [scanKey]
  );

  const run = React.useCallback(
    async (opts?: { manual?: boolean }) => {
      if (!orgId || !state.sourceRepo) return;
      const version = ++requestVersion.current;
      const update = (apply: (current: S) => S) =>
        setState((current) => {
          const currentKey = JSON.stringify([
            orgId,
            current.sourceRepo,
            current.sourceKind,
            current.ref || current.defaultBranch || "main",
          ]);
          return version === requestVersion.current && currentKey === scanKey
            ? apply(current)
            : current;
        });
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
      }).catch((error: unknown) => ({
        data: undefined,
        error: error instanceof Error ? error : new Error(String(error)),
      }));
      if (version !== requestVersion.current) return;
      if (error) {
        setFetchState("error");
        setFetchError(error.message);
        update((s) => ({ ...s, scanned: false, discoveredAgents: [], scanError: error.message }));
        if (manual) toast.error(`Scan failed: ${error.message}`);
        return;
      }
      const result = data?.scanAgentManifests;
      if (!result) {
        setFetchState("error");
        setFetchError("no response from server");
        update((s) => ({ ...s, scanned: false, discoveredAgents: [], scanError: "no response" }));
        if (manual) toast.error("Scan failed: no response from server.");
        return;
      }
      if (!result.ok) {
        const msg = result.error ?? "scan failed";
        setFetchState("error");
        setFetchError(msg);
        update((s) => ({ ...s, scanned: false, discoveredAgents: [], scanError: msg }));
        if (manual) toast.error(`Scan failed: ${msg}`);
        return;
      }
      const agents = result.agents;
      // Default-select every not-yet-registered agent. Already-registered
      // agents are pre-checked + disabled (they'll be matched, not
      // duplicated). The selection is a client-side preview — see the
      // hook docstring.
      update((s) => ({
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
    [
      orgId,
      state.sourceRepo,
      state.sourceKind,
      state.ref,
      state.defaultBranch,
      scanKey,
      scan,
      setState,
    ]
  );

  // Auto-scan on first entry (or whenever the repo / ref changed upstream).
  const lastScanKey = React.useRef<string>("");
  React.useEffect(() => {
    const key = scanKey;
    if (!orgId || !state.sourceRepo) return;
    if (state.scanned && lastScanKey.current === key) return;
    if (lastScanKey.current === key && fetchState !== "idle") return;
    lastScanKey.current = key;
    void run();
  }, [orgId, state.sourceRepo, scanKey, state.scanned, fetchState, run]);

  // Valid once we've discovered at least one not-yet-registered agent. (If
  // every discovered agent is already registered there's nothing to do — the
  // single-repo path with one new agent satisfies acceptance (4).)
  const newAgentCount = state.discoveredAgents.filter((a) => !a.alreadyRegistered).length;
  React.useEffect(() => {
    setValid(state.scanned && newAgentCount > 0);
  }, [state.scanned, newAgentCount, setValid]);

  const toggle = React.useCallback(
    (manifestPath: string) => {
      setState((s) => {
        const has = s.selectedManifestPaths.includes(manifestPath);
        return {
          ...s,
          selectedManifestPaths: has
            ? s.selectedManifestPaths.filter((p) => p !== manifestPath)
            : [...s.selectedManifestPaths, manifestPath],
        };
      });
    },
    [setState]
  );

  const rescan = React.useCallback(() => {
    void run({ manual: true });
  }, [run]);

  return { fetchState, fetchError, newAgentCount, toggle, rescan };
}
