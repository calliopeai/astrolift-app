"use client";

import { useLazyQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  BotIcon,
  CheckCircle2Icon,
  FileTextIcon,
  FileXIcon,
  PencilIcon,
  RefreshCwIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { GET_SOURCE_FILE } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceFile } from "@/graphql/scm/scm.types";
import { generateFriendlySlug } from "@/lib/friendly-name";
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

/**
 * Client-side mirror of the backend ``detect_toml_schema`` agent-config check
 * (#1172): an agent config repo carries ``astrolift_version`` plus a
 * ``[skills.*]`` / ``[tools.*]`` table and has neither a top-level ``name``
 * nor a ``[[workloads]]`` block. Regex-grade, same posture as
 * ``validateManifest`` — the backend is authoritative; this only decides
 * whether to surface the "wrong wizard" callout. These two schemas share the
 * ``astrolift.toml`` filename, so the manifest step would otherwise validate
 * an agent library as a broken app manifest.
 */
function looksLikeAgentConfig(raw: string): boolean {
  const trimmed = raw.trim();
  if (!trimmed) return false;
  const hasVersion = /^astrolift_version\s*=/m.test(trimmed);
  const hasSkillOrToolTable = /^\s*\[\s*(?:skills|tools)\s*\./m.test(trimmed);
  const hasWorkloads = /^\s*\[\[\s*workloads\s*\]\]/m.test(trimmed);
  const beforeFirstSection = trimmed.split(/\n\[/)[0];
  const hasName = /^name\s*=\s*["'][^"'\n]+["']/m.test(beforeFirstSection);
  return hasVersion && hasSkillOrToolTable && !hasWorkloads && !hasName;
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

  const agentConfigDetected = looksLikeAgentConfig(state.manifestRaw);
  const isReadOnly = (fetchState === "found" && !editing) || state.manifestLater;

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
            the repository root. Monorepos can host multiple apps — point each registration at its
            own path (e.g. <code className="font-mono">services/api/astrolift.toml</code>).
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

      <label className="flex items-start gap-3 rounded-md border p-4">
        <input
          type="checkbox"
          className="mt-1"
          checked={state.manifestLater}
          onChange={(e) => setState((s) => ({ ...s, manifestLater: e.target.checked }))}
        />
        <div className="flex-1">
          <span className="font-medium">Set up manifest later</span>
          <p className="text-muted-foreground mt-1 text-xs">
            Register the app now without a manifest. We&apos;ll land you on the app&apos;s{" "}
            <span className="font-medium">Manifest</span> tab, where you can add or sync{" "}
            <code className="font-mono">astrolift.toml</code> when you&apos;re ready. Useful for an
            empty repo, or an agent config repo whose schema isn&apos;t an app manifest.
          </p>
        </div>
      </label>

      <div className={cn("space-y-2", state.manifestLater && "opacity-60")}>
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
              onClick={() => void auto({ manual: true })}
              disabled={fetchState === "fetching"}
              title="Test connection & re-fetch the manifest from the picked repo"
            >
              <RefreshCwIcon
                className={cn("size-3.5", fetchState === "fetching" && "animate-spin")}
              />
              {fetchState === "fetching" ? "Fetching…" : "Test & re-fetch"}
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
        {agentConfigDetected && !state.manifestLater && (
          <div className="border-info-border bg-info/10 flex flex-col gap-2 rounded-md border p-3 text-sm">
            <div className="flex items-center gap-2">
              <BotIcon className="text-info-fg size-4" />
              <span className="font-medium">This looks like an agent config repo</span>
            </div>
            <p className="text-muted-foreground text-xs">
              It declares <code className="font-mono">astrolift_version</code> with{" "}
              <code className="font-mono">[skills.*]</code>/
              <code className="font-mono">[tools.*]</code> tables and no{" "}
              <code className="font-mono">[[workloads]]</code> — that&apos;s the agent library
              schema, not an app manifest. Onboard it as an agent fleet or import its skills and
              tools instead:
            </p>
            <div className="flex flex-wrap gap-2">
              <Button asChild size="sm" variant="outline">
                <Link href="/agents/new">Onboard an agent fleet</Link>
              </Button>
              <Button asChild size="sm" variant="outline">
                <Link href="/agents/skills/import">Import skills / tools</Link>
              </Button>
            </div>
            <p className="text-muted-foreground text-2xs">
              Or tick <span className="font-medium">Set up manifest later</span> above to register
              this repo without a manifest.
            </p>
          </div>
        )}
        {state.manifestErrors.length > 0 && (
          <ul className="text-destructive list-inside list-disc space-y-1 text-xs">
            {state.manifestErrors.map((err, i) => (
              <li key={i}>{err}</li>
            ))}
          </ul>
        )}
        {state.manifestValid && (
          <p className="text-success-fg inline-flex items-center gap-1 text-xs">
            <CheckCircle2Icon className="size-3.5" />
            Manifest looks structurally valid (top-level <code className="font-mono">name</code> +
            at least one <code className="font-mono">[[workloads]]</code> block).
          </p>
        )}
      </div>
      {state.manifestLater && (
        <p className="text-muted-foreground inline-flex items-center gap-1 text-xs">
          <CheckCircle2Icon className="text-info-fg size-3.5" />
          Manifest step skipped — the app registers without a manifest. Add or sync it on the
          app&apos;s Manifest tab after onboarding.
        </p>
      )}
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
      <div className="border-success-border bg-success/10 flex items-center gap-2 rounded-md border p-3 text-sm">
        <CheckCircle2Icon className="text-success-fg size-4" />
        <span className="flex-1">Loaded manifest from the repo.</span>
        <Badge variant="secondary" className="gap-1">
          <FileTextIcon className="size-3" /> from repo
        </Badge>
      </div>
    );
  }
  if (state === "missing") {
    return (
      <div className="border-warning-border bg-warning/10 flex items-center gap-2 rounded-md border p-3 text-sm">
        <FileXIcon className="text-warning-fg size-4" />
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
