"use client";

import { useLazyQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  CheckCircle2Icon,
  FileTextIcon,
  FileXIcon,
  PencilIcon,
  RefreshCwIcon,
} from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { GET_SOURCE_FILE } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceFile } from "@/graphql/scm/scm.types";
import { cn } from "@/lib/utils";

import type { WizardState } from "../wizard-client";

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

interface Props {
  state: WizardState;
  setState: React.Dispatch<React.SetStateAction<WizardState>>;
  setValid: (valid: boolean) => void;
}

type FetchState = "idle" | "fetching" | "found" | "missing" | "error";

export function ManifestPreviewStep({ state, setState, setValid }: Props) {
  const [fetchState, setFetchState] = React.useState<FetchState>("idle");
  const [fetchError, setFetchError] = React.useState<string>("");
  const [editing, setEditing] = React.useState(false);
  const [fetchManifest] = useLazyQuery<SourceFileResp>(GET_SOURCE_FILE, {
    fetchPolicy: "network-only",
  });

  const auto = React.useCallback(async () => {
    if (!state.connectionId || !state.sourceRepo) return;
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
      return;
    }
    const f = data?.astroliftSourceFile;
    if (!f) {
      setFetchState("error");
      setFetchError("no response from server");
      return;
    }
    if (f.errorCode) {
      setFetchState("error");
      setFetchError(f.errorMessage ?? f.errorCode);
      return;
    }
    if (f.content == null) {
      // Not found — seed the editor with the template.
      setFetchState("missing");
      setEditing(true);
      setState((s) => ({
        ...s,
        manifestRaw: s.manifestRaw || TEMPLATE.replace(/REPLACE-ME/g, s.slug || s.name || "my-app"),
        manifestFromRepo: false,
      }));
      return;
    }
    setFetchState("found");
    setState((s) => ({
      ...s,
      manifestRaw: f.content ?? "",
      manifestFromRepo: true,
    }));
  }, [
    state.connectionId,
    state.sourceRepo,
    state.manifestPath,
    state.defaultBranch,
    fetchManifest,
    setState,
  ]);

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
    setValid(ok);
  }, [state.manifestRaw, setState, setValid]);

  const isReadOnly = fetchState === "found" && !editing;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-2">
          <Label htmlFor="manifest-path">Manifest path</Label>
          <Input
            id="manifest-path"
            value={state.manifestPath}
            onChange={(e) => setState((s) => ({ ...s, manifestPath: e.target.value }))}
            className="font-mono text-xs"
          />
          <p className="text-muted-foreground text-xs">
            Where <code className="font-mono">astrolift.toml</code> lives in the repo. Defaults to
            the repository root.
          </p>
        </div>
        <div className="space-y-2">
          <Label htmlFor="default-branch">Default branch</Label>
          <Input
            id="default-branch"
            value={state.defaultBranch}
            onChange={(e) => setState((s) => ({ ...s, defaultBranch: e.target.value }))}
            className="font-mono text-xs"
          />
        </div>
      </div>

      <FetchBanner state={fetchState} error={fetchError} onRetry={auto} />

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="manifest-raw">Manifest</Label>
          <div className="flex items-center gap-1">
            {fetchState === "found" && !editing && (
              <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(true)}>
                <PencilIcon className="size-3.5" />
                Edit
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => void auto()}
              disabled={fetchState === "fetching"}
            >
              <RefreshCwIcon
                className={cn("size-3.5", fetchState === "fetching" && "animate-spin")}
              />
              {fetchState === "fetching" ? "Fetching…" : "Re-fetch"}
            </Button>
          </div>
        </div>
        <Textarea
          id="manifest-raw"
          value={state.manifestRaw}
          readOnly={isReadOnly}
          onChange={(e) => {
            setState((s) => ({
              ...s,
              manifestRaw: e.target.value,
              manifestFromRepo: false,
            }));
          }}
          rows={16}
          className={cn("font-mono text-xs", isReadOnly && "bg-muted/40 cursor-default")}
          placeholder="Paste an astrolift.toml manifest here."
        />
        {state.manifestErrors.length > 0 && (
          <ul className="text-destructive list-inside list-disc space-y-1 text-xs">
            {state.manifestErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        )}
        {state.manifestValid && (
          <p className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <CheckCircle2Icon className="size-3.5" />
            Manifest looks structurally valid (top-level <code className="font-mono">name</code> +
            at least one <code className="font-mono">[[workloads]]</code> block).
          </p>
        )}
      </div>
    </div>
  );
}

function FetchBanner({
  state,
  error,
  onRetry,
}: {
  state: FetchState;
  error: string;
  onRetry: () => void;
}) {
  if (state === "idle" || state === "fetching") return null;
  if (state === "found") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
        <CheckCircle2Icon className="size-4 text-emerald-600 dark:text-emerald-400" />
        <span className="flex-1">Loaded manifest from the repo.</span>
        <Badge variant="secondary" className="gap-1">
          <FileTextIcon className="size-3" /> from repo
        </Badge>
      </div>
    );
  }
  if (state === "missing") {
    return (
      <div className="flex items-center gap-2 rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
        <FileXIcon className="size-4 text-amber-600 dark:text-amber-400" />
        <span className="flex-1">
          No manifest at that path. We&apos;ve seeded a minimal template — edit it below or paste
          your own.
        </span>
      </div>
    );
  }
  return (
    <div className="border-destructive/40 bg-destructive/10 flex items-center gap-2 rounded-md border p-3 text-sm">
      <AlertTriangleIcon className="text-destructive size-4" />
      <span className="flex-1">Couldn&apos;t fetch the manifest — {error}</span>
      <Button type="button" size="sm" variant="outline" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}

function sameErrors(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}
