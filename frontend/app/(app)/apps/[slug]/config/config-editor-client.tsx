"use client";

import { useMutation, useQuery } from "@apollo/client/react";
import {
  AlertTriangleIcon,
  ExternalLinkIcon,
  GitPullRequestIcon,
  RefreshCwIcon,
  SaveIcon,
} from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import type { MutationResult } from "@/graphql/identity/identity.types";
import {
  PUSH_MANIFEST_TO_REPO,
  SYNC_MANIFEST_FROM_REPO,
  UPDATE_MANIFEST,
} from "@/graphql/registry/registry.mutations";
import {
  GET_APP,
  GET_RENDERED_MANIFEST,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftRegisteredApp,
  ManifestSyncState,
} from "@/graphql/registry/registry.types";
import { useFormatters } from "@/lib/i18n/formatters";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface ManifestStagePayload {
  id: string;
  syncState: string;
  rawManifest: string;
  rawManifestStaged: string;
}

interface ManifestPushPayload {
  id: string;
  prUrl: string;
  branchName: string;
  note: string;
}

interface RenderedResp {
  astroliftRenderedManifest: {
    appSlug: string;
    namespace: string;
    resources: Record<string, unknown>;
    error?: string | null;
    errorPath?: string | null;
    errorLine?: number | null;
    errorColumn?: number | null;
  } | null;
}

const SYNC_BADGE: Record<
  ManifestSyncState,
  { label: string; tone: "default" | "secondary" | "destructive" | "outline" }
> = {
  in_sync: { label: "In sync", tone: "secondary" },
  db_ahead: { label: "Unsaved drafts in DB", tone: "outline" },
  repo_ahead: { label: "Repo ahead", tone: "outline" },
  diverged: { label: "Diverged", tone: "destructive" },
};

// Section keys map to the leading [section] header in astrolift.toml.
// The pill toggle scrolls + selects the first occurrence inside the
// textarea — for the workloads/services/env/volumes tables which are
// always written as [[workloads.web]] / [services.postgres] / etc.,
// scanning for "[<key>" catches both the bare section and the inline
// array-of-tables forms.
const PILL_SECTIONS: Array<{ key: string; label: string; headers: string[] }> = [
  { key: "workloads", label: "Workloads", headers: ["[workloads", "[[workloads"] },
  { key: "services", label: "Managed services", headers: ["[services", "[[services"] },
  { key: "env", label: "Env vars", headers: ["[env", "[[env"] },
  { key: "volumes", label: "Volumes", headers: ["[volumes", "[[volumes"] },
];

function PillToggleGroup({
  onSelect,
  active,
}: {
  onSelect: (key: string) => void;
  active: string | null;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {PILL_SECTIONS.map((p) => {
        const isActive = p.key === active;
        return (
          <button
            key={p.key}
            type="button"
            onClick={() => onSelect(p.key)}
            className={`rounded-full border px-3 py-1 text-xs transition-colors ${
              isActive
                ? "border-foreground bg-foreground text-background"
                : "border-input hover:bg-accent hover:text-accent-foreground"
            }`}
          >
            {p.label}
          </button>
        );
      })}
    </div>
  );
}

function ConflictResolverModal({
  ours,
  theirs,
  serverUpdatedAt,
  onForceOverwrite,
  onAcceptTheirs,
  onClose,
}: {
  ours: string;
  theirs: string;
  serverUpdatedAt: string;
  onForceOverwrite: () => void;
  onAcceptTheirs: () => void;
  onClose: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-6"
      role="dialog"
      aria-modal="true"
      aria-labelledby="conflict-title"
    >
      <div className="bg-background w-full max-w-4xl rounded-lg border p-6 shadow-2xl">
        <h2 id="conflict-title" className="text-lg font-semibold">
          The manifest changed under your edit
        </h2>
        <p className="text-muted-foreground mt-1 text-sm">
          The server&apos;s copy was updated at{" "}
          <span className="font-mono">{new Date(serverUpdatedAt).toLocaleString()}</span>.
          Keep your edit (force-overwrite their changes) or discard your draft
          and load theirs.
        </p>
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          <div>
            <div className="text-muted-foreground mb-1 text-xs uppercase tracking-wider">
              Your draft
            </div>
            <pre className="bg-muted max-h-[50vh] overflow-auto rounded p-3 font-mono text-[11px] leading-relaxed">
              {ours}
            </pre>
          </div>
          <div>
            <div className="text-muted-foreground mb-1 text-xs uppercase tracking-wider">
              Server copy
            </div>
            <pre className="bg-muted max-h-[50vh] overflow-auto rounded p-3 font-mono text-[11px] leading-relaxed">
              {theirs}
            </pre>
          </div>
        </div>
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="outline" onClick={onAcceptTheirs}>
            Discard mine, load theirs
          </Button>
          <Button variant="destructive" onClick={onForceOverwrite}>
            Force-overwrite with mine
          </Button>
        </div>
      </div>
    </div>
  );
}

export function ConfigEditorClient({ slug }: { slug: string }) {
  const fmt = useFormatters();
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp ?? null;

  // Editor draft. Staged content takes precedence over raw because
  // it represents the most recent unsaved-to-repo state.
  const initialDraft = a?.rawManifestStaged?.length
    ? a.rawManifestStaged
    : (a?.rawManifest ?? "");
  const [draft, setDraft] = React.useState<string>(initialDraft);
  const [draftLoaded, setDraftLoaded] = React.useState(false);
  const [activePill, setActivePill] = React.useState<string | null>(null);
  const textareaRef = React.useRef<HTMLTextAreaElement>(null);

  // Snapshot of the server's "effective" manifest at the moment the
  // operator started editing. We compare against this on every refetch
  // to detect "someone else changed it under me" conflicts. The check
  // fires when (a) we have a draft locally that differs from the
  // server's current effective text AND (b) the server's effective text
  // also differs from the snapshot — meaning both sides moved.
  const baselineRef = React.useRef<string>("");
  const [conflict, setConflict] = React.useState<{
    theirs: string;
    serverUpdatedAt: string;
  } | null>(null);

  React.useEffect(() => {
    if (a && !draftLoaded) {
      setDraft(initialDraft);
      baselineRef.current = initialDraft;
      setDraftLoaded(true);
    }
    // initialDraft derives from `a`; pin on `a.id`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a?.id]);

  // Conflict detection. Runs whenever the server payload changes —
  // refetchQueries from our own mutations are no-ops here because we
  // also reset baselineRef on save/sync. A change driven by *another*
  // editor (different tab / different user) shows up as: server text
  // moved away from our baseline, and our draft also moved away.
  React.useEffect(() => {
    if (!a || !draftLoaded) return;
    const serverEffective = a.rawManifestStaged?.length
      ? a.rawManifestStaged
      : (a.rawManifest ?? "");
    const baseline = baselineRef.current;
    if (serverEffective === baseline) return; // server hasn't moved
    if (draft === baseline) {
      // We haven't edited yet — silently roll forward.
      setDraft(serverEffective);
      baselineRef.current = serverEffective;
      return;
    }
    if (draft === serverEffective) {
      // We somehow already match — adopt the server timestamp.
      baselineRef.current = serverEffective;
      return;
    }
    // Both moved: real conflict.
    setConflict({
      theirs: serverEffective,
      serverUpdatedAt: a.updatedAt ?? new Date().toISOString(),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a?.rawManifest, a?.rawManifestStaged, a?.updatedAt]);

  const rendered = useQuery<RenderedResp>(GET_RENDERED_MANIFEST, {
    variables: { appSlug: slug, environmentName: null, imageTag: null },
    skip: !a,
    fetchPolicy: "cache-and-network",
  });

  const refetch = [{ query: GET_APP, variables: { slug } }];
  const [updateManifest, updateState] = useMutation<{
    updateManifest: MutationResult<ManifestStagePayload>;
  }>(UPDATE_MANIFEST, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [syncManifest, syncState] = useMutation<{
    syncManifestFromRepo: MutationResult<ManifestStagePayload>;
  }>(SYNC_MANIFEST_FROM_REPO, { refetchQueries: refetch, awaitRefetchQueries: true });
  const [pushManifest, pushState] = useMutation<{
    pushManifestToRepo: MutationResult<ManifestPushPayload>;
  }>(PUSH_MANIFEST_TO_REPO, { refetchQueries: refetch, awaitRefetchQueries: true });

  const busy = updateState.loading || syncState.loading || pushState.loading;

  async function handleSave() {
    if (!a) return;
    const { data } = await updateManifest({
      variables: { input: { id: a.id, rawManifest: draft } },
    });
    if (data?.updateManifest.ok) {
      baselineRef.current = draft;
      toast.success("Draft saved");
    } else {
      toast.error(data?.updateManifest.errors?.[0]?.message ?? "Save failed");
    }
  }

  async function handleSync() {
    if (!a) return;
    if (
      !confirm(
        "Sync from repo? Discards any unsaved draft and pulls the source manifest as the new base.",
      )
    ) {
      return;
    }
    const { data } = await syncManifest({ variables: { input: { id: a.id } } });
    if (data?.syncManifestFromRepo.ok) {
      const next = data.syncManifestFromRepo.data;
      const text = next?.rawManifestStaged || next?.rawManifest || "";
      setDraft(text);
      baselineRef.current = text;
      toast.success("Synced from repo");
    } else {
      toast.error(data?.syncManifestFromRepo.errors?.[0]?.message ?? "Sync failed");
    }
  }

  async function handlePush() {
    if (!a) return;
    const { data } = await pushManifest({ variables: { input: { id: a.id } } });
    const result = data?.pushManifestToRepo;
    if (result?.ok) {
      const note = result.data?.note;
      if (note === "nothing_to_push") {
        toast.message("No staged changes to push.");
      } else if (result.data?.prUrl) {
        toast.success(`PR opened: ${result.data.branchName}`);
      } else {
        toast.success("Push complete");
      }
    } else {
      toast.error(result?.errors?.[0]?.message ?? "Push failed");
    }
  }

  function scrollToSection(key: string) {
    const section = PILL_SECTIONS.find((p) => p.key === key);
    if (!section || !textareaRef.current) return;
    const ta = textareaRef.current;
    const text = ta.value;
    // Find the first occurrence of any header prefix on a line. We
    // walk line-by-line so a "[workloads" inside a comment or value
    // doesn't false-match.
    const lines = text.split("\n");
    let charOffset = 0;
    let foundLine = -1;
    for (let i = 0; i < lines.length; i++) {
      const trimmed = lines[i].trimStart();
      if (section.headers.some((h) => trimmed.startsWith(h))) {
        foundLine = i;
        break;
      }
      charOffset += lines[i].length + 1;
    }
    if (foundLine === -1) {
      toast.message(`No ${section.label.toLowerCase()} section in this manifest yet.`);
      return;
    }
    setActivePill(key);
    ta.focus();
    // Set selection to the header line so the textarea scrolls to it.
    const headerEnd = charOffset + lines[foundLine].length;
    ta.setSelectionRange(charOffset, headerEnd);
    // Manual scroll — chromium ignores setSelectionRange for scroll in
    // some configurations. Approximate by setting scrollTop based on
    // line height.
    const lineHeight = parseFloat(
      window.getComputedStyle(ta).lineHeight || "16",
    );
    ta.scrollTop = Math.max(0, foundLine * lineHeight - 40);
  }

  function dismissConflictKeepMine() {
    if (!conflict) return;
    // Treat our current draft as the new baseline so the next refetch
    // doesn't immediately re-fire. The next Save will write our draft.
    baselineRef.current = conflict.theirs;
    setConflict(null);
  }

  function adoptTheirs() {
    if (!conflict) return;
    setDraft(conflict.theirs);
    baselineRef.current = conflict.theirs;
    setConflict(null);
    toast.message("Loaded the server's copy.");
  }

  if (app.loading && !a) {
    return (
      <PageShell title="Config" description="Loading…">
        <Skeleton className="h-96 w-full" />
      </PageShell>
    );
  }

  if (!a) {
    return (
      <PageShell
        title="App not found"
        description="The app doesn't exist or you don't have permission to view it."
      >
        <EmptyState
          icon={<AlertTriangleIcon className="size-5" />}
          title={`No app with slug ${slug}`}
          actionHref="/apps"
          actionLabel="Back to apps"
        />
      </PageShell>
    );
  }

  const syncBadge = SYNC_BADGE[a.manifestSyncState] ?? SYNC_BADGE.in_sync;
  const isDirty =
    draft !== (a.rawManifestStaged?.length ? a.rawManifestStaged : a.rawManifest);
  const renderedResult = rendered.data?.astroliftRenderedManifest;

  return (
    <PageShell
      title={`${a.name} · config`}
      description={
        <span className="flex flex-wrap items-center gap-2">
          <span>{a.manifestPath} on {a.deployBranch}</span>
          <Badge variant={syncBadge.tone}>{syncBadge.label}</Badge>
          <span className="text-muted-foreground text-xs">
            updated {fmt.formatRelativeTime(a.updatedAt)}
          </span>
        </span>
      }
      actions={
        <>
          <Button
            variant="outline"
            onClick={handleSync}
            disabled={busy}
          >
            <RefreshCwIcon className="size-4" />
            Sync from repo
          </Button>
          <Button onClick={handleSave} disabled={busy || !isDirty}>
            <SaveIcon className="size-4" />
            {updateState.loading ? "Saving…" : "Save draft"}
          </Button>
          <Button
            variant="outline"
            onClick={handlePush}
            disabled={busy || a.manifestSyncState === "in_sync"}
          >
            <GitPullRequestIcon className="size-4" />
            Push to repo
          </Button>
        </>
      }
    >
      {conflict && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
            <div className="flex items-center gap-2">
              <AlertTriangleIcon className="text-destructive size-4" />
              <span>
                The manifest was updated on the server{" "}
                <span className="font-medium">
                  {fmt.formatRelativeTime(conflict.serverUpdatedAt)}
                </span>
                . Your edit is based on an older copy.
              </span>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="outline" onClick={dismissConflictKeepMine}>
                Keep mine
              </Button>
              <Button size="sm" variant="outline" onClick={adoptTheirs}>
                Load theirs
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2">
        <PillToggleGroup onSelect={scrollToSection} active={activePill} />
        <p className="text-muted-foreground text-xs">
          Jump to a section in the editor.
        </p>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">
              <span className="font-mono">{a.manifestPath}</span>
              {isDirty && (
                <Badge variant="outline" className="ml-2 text-[10px]">
                  unsaved
                </Badge>
              )}
            </CardTitle>
            <CardDescription>
              Source TOML. Save stages the change in the platform; Push to repo opens a PR.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={26}
              spellCheck={false}
              className="font-mono text-xs"
              placeholder="# astrolift.toml"
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Rendered preview</CardTitle>
            <CardDescription>
              Kubernetes resources the platform would apply for this manifest. Re-renders on the saved draft, not the in-flight edit.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            {rendered.loading && !rendered.data ? (
              <Skeleton className="m-6 h-96" />
            ) : renderedResult?.error ? (
              <div className="text-destructive p-6 text-sm">
                <p className="font-medium">Render failed</p>
                <p className="mt-1">{renderedResult.error}</p>
                {renderedResult.errorPath && (
                  <p className="text-muted-foreground mt-2 font-mono text-xs">
                    {renderedResult.errorPath}
                    {renderedResult.errorLine != null &&
                      `:${renderedResult.errorLine}`}
                    {renderedResult.errorColumn != null &&
                      `:${renderedResult.errorColumn}`}
                  </p>
                )}
              </div>
            ) : renderedResult ? (
              <pre className="bg-muted m-4 max-h-[640px] overflow-auto rounded p-3 font-mono text-xs leading-relaxed">
                {JSON.stringify(renderedResult.resources, null, 2)}
              </pre>
            ) : (
              <div className="text-muted-foreground p-6 text-sm">
                No rendered output yet.
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {a.sourceUrl && (
        <Card className="border-dashed">
          <CardContent className="flex items-center justify-between gap-3 p-4 text-sm">
            <span className="text-muted-foreground">
              Source repo:{" "}
              <span className="font-mono">{a.sourceRepo}</span> on branch{" "}
              <span className="font-mono">{a.deployBranch}</span>
            </span>
            <Button asChild variant="ghost" size="sm">
              <a href={a.sourceUrl} target="_blank" rel="noreferrer">
                <ExternalLinkIcon className="size-3.5" />
                Open repo
              </a>
            </Button>
          </CardContent>
        </Card>
      )}

      {conflict && (
        <ConflictResolverModal
          ours={draft}
          theirs={conflict.theirs}
          serverUpdatedAt={conflict.serverUpdatedAt}
          onForceOverwrite={dismissConflictKeepMine}
          onAcceptTheirs={adoptTheirs}
          onClose={() => setConflict(null)}
        />
      )}
    </PageShell>
  );
}
