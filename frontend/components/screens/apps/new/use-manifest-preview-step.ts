"use client";

import { useLazyQuery } from "@apollo/client/react";
import * as React from "react";
import { toast } from "sonner";

import { GET_SOURCE_FILE } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceFile } from "@/graphql/scm/scm.types";
import { generateFriendlySlug } from "@/lib/friendly-name";

import type { ManifestFetchState, ManifestPreviewFields } from "./ManifestPreviewStep";

interface SourceFileResp {
  astroliftSourceFile: AstroliftSourceFile;
}

const TEMPLATE = `# astrolift.toml — minimal app manifest
name = "REPLACE-ME"

[[workloads]]
slug = "web"
kind = "deployment"
replicas = 1

[workloads.containers.app]
image = "ghcr.io/example/REPLACE-ME"
port = 8080

# [env]
# DATABASE_URL = { from = "managed_service", service = "postgres" }
`;

/**
 * Naive TOML shape validation — we don't ship a TOML parser, so this
 * is a regex-grade check that the manifest looks structurally
 * reasonable. The backend validator is authoritative; this just
 * blocks "obvious nonsense" before we let the operator advance.
 */
function validateManifest(raw: string): {
  ok: boolean;
  errors: string[];
} {
  const errors: string[] = [];
  const trimmed = raw.trim();
  if (!trimmed) {
    errors.push("Manifest is empty.");
    return { ok: false, errors };
  }
  // Top-level `name = "..."` (single or double quotes, anywhere before any
  // `[section]` marker)
  const beforeFirstSection = trimmed.split(/\n\[/)[0];
  if (!/^name\s*=\s*["'][^"'\n]+["']/m.test(beforeFirstSection)) {
    errors.push('Top-level `name = "..."` is required.');
  }
  // At least one [[workloads]] block
  if (!/^\s*\[\[\s*workloads\s*\]\]/m.test(trimmed)) {
    errors.push("At least one `[[workloads]]` block is required.");
  }
  return { ok: errors.length === 0, errors };
}

/** The wizard-state fields the fetch + validation logic reads. */
export interface ManifestPreviewLogicFields extends ManifestPreviewFields {
  connectionId: string;
  sourceRepo: string;
  slug: string;
  name: string;
}

/**
 * Wizard step 2 logic: fetches the manifest from the picked repo (seeding a
 * template when none exists), re-validates on every edit, and reports step
 * validity up to the wizard.
 */
export function useManifestPreviewStep<S extends ManifestPreviewLogicFields>(
  state: S,
  setState: React.Dispatch<React.SetStateAction<S>>,
  setValid: (valid: boolean) => void
) {
  const [fetchState, setFetchState] = React.useState<ManifestFetchState>("idle");
  const [fetchError, setFetchError] = React.useState<string>("");
  const [editing, setEditing] = React.useState(false);
  const [fetchManifest] = useLazyQuery<SourceFileResp>(GET_SOURCE_FILE, {
    fetchPolicy: "network-only",
  });

  const auto = React.useCallback(
    async (opts?: { manual?: boolean }) => {
      if (!state.connectionId || !state.sourceRepo) return;
      const manual = opts?.manual ?? false;
      setFetchState("fetching");
      setFetchError("");
      const { data, error } = await fetchManifest({
        variables: {
          connectionId: state.connectionId,
          repoFullName: state.sourceRepo,
          path: state.manifestPath || "astrolift.toml",
          ref: state.defaultBranch || "main",
        },
      });
      if (error) {
        setFetchState("error");
        setFetchError(error.message);
        if (manual) toast.error(`Manifest refetch failed: ${error.message}`);
        return;
      }
      const f = data?.astroliftSourceFile;
      if (!f) {
        setFetchState("error");
        setFetchError("no response from server");
        if (manual) toast.error("Manifest refetch failed: no response from server.");
        return;
      }
      if (f.errorCode) {
        setFetchState("error");
        setFetchError(f.errorMessage ?? f.errorCode);
        if (manual) {
          toast.error(`Manifest refetch failed: ${f.errorMessage ?? f.errorCode}`);
        }
        return;
      }
      if (f.content == null) {
        // Not found — seed the editor with the template.
        setFetchState("missing");
        setEditing(true);
        setState((s) => ({
          ...s,
          manifestRaw:
            s.manifestRaw ||
            TEMPLATE.replace(/REPLACE-ME/g, s.slug || s.name || generateFriendlySlug()),
          manifestFromRepo: false,
        }));
        if (manual) {
          toast.message("No manifest at that path — seeded a template you can edit.");
        }
        return;
      }
      setFetchState("found");
      setState((s) => ({
        ...s,
        manifestRaw: f.content ?? "",
        manifestFromRepo: true,
      }));
      if (manual) toast.success("Manifest re-fetched from repo.");
    },
    [
      state.connectionId,
      state.sourceRepo,
      state.manifestPath,
      state.defaultBranch,
      fetchManifest,
      setState,
    ]
  );

  // Auto-fetch when entering the step the first time (or whenever the
  // repo / manifest path / branch changes upstream).
  const lastFetchKey = React.useRef<string>("");
  React.useEffect(() => {
    const key = `${state.connectionId}|${state.sourceRepo}|${state.manifestPath}|${state.defaultBranch}`;
    if (!state.connectionId || !state.sourceRepo) return;
    if (lastFetchKey.current === key) return;
    lastFetchKey.current = key;
    void auto();
  }, [state.connectionId, state.sourceRepo, state.manifestPath, state.defaultBranch, auto]);

  // Re-validate on every manifest change.
  React.useEffect(() => {
    const { ok, errors } = validateManifest(state.manifestRaw);
    setState((s) =>
      s.manifestValid === ok && sameErrors(s.manifestErrors, errors)
        ? s
        : { ...s, manifestValid: ok, manifestErrors: errors }
    );
    // "Set up manifest later" lets the step advance without a valid manifest
    // (#1172); we still track manifestValid/errors above so the review step
    // reflects reality and toggling the option back off restores the gate.
    setValid(state.manifestLater || ok);
  }, [state.manifestRaw, state.manifestLater, setState, setValid]);

  return { fetchState, fetchError, editing, setEditing, refetch: auto };
}

function sameErrors(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}
