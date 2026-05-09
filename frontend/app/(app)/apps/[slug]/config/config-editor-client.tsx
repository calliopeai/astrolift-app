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

  React.useEffect(() => {
    if (a && !draftLoaded) {
      setDraft(initialDraft);
      setDraftLoaded(true);
    }
    // initialDraft derives from `a`; pin on `a.id`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a?.id]);

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
      if (next) setDraft(next.rawManifestStaged || next.rawManifest || "");
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
    </PageShell>
  );
}
