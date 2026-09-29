"use client";

import * as React from "react";
import { BotIcon, ExpandIcon, Loader2Icon, MonitorPlayIcon, RefreshCwIcon } from "lucide-react";

import { EmptyState } from "@/components/EmptyState";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { VncViewer } from "@/components/observability/VncViewer";
import { cn } from "@/lib/utils";

import type { useAgentGallery } from "./use-agent-gallery";

// Roster refresh cadence. Doubles as the snapshot-frame cadence: each poll
// re-mints the presigned snapshot GET URL, so a fresh (signed) URL string
// arrives every interval and the tile <img> re-fetches the latest frame
// the pod-side uploader overwrote in place. (We can't cache-bust a single
// presigned URL with an extra query param — that breaks the signature — so
// re-presigning on the poll is the frame-advance mechanism.)
export const GALLERY_POLL_MS = 5000;

// A watchable agent task as returned by the agentGallery query.
export interface GalleryTask {
  id: string;
  status: string;
  startedAt: string | null;
  vncEnabled: boolean;
  vncUrl: string;
  snapshotUrl: string | null;
}

function shortId(id: string): string {
  return id.slice(0, 8);
}

function elapsedLabel(startedAt: string | null): string {
  if (!startedAt) return "just now";
  const secs = Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000);
  if (secs < 60) return `${Math.max(secs, 0)}s`;
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ${secs % 60}s`;
  const hrs = Math.floor(mins / 60);
  return `${hrs}h ${mins % 60}m`;
}

/**
 * The VNC "theatre" — a gallery of running watchable agents rendered as
 * snapshot tiles that explode into a live RFB session.
 *
 * Tiles show the latest framebuffer JPEG (polled via a re-minted presigned
 * GET every {@link GALLERY_POLL_MS}). Clicking a tile opens a fullscreen theatre
 * modal that connects the live noVNC session at the task's ``vncUrl`` via
 * the existing {@link VncViewer}. Each tile also offers a pop-out into
 * ``/agents/runs/<task>/vnc`` for a dedicated tab.
 */
export type AgentTheatreProps = ReturnType<typeof useAgentGallery>;

export function AgentTheatre({
  hasOrg,
  tasks: roster,
  loading,
  error,
  refreshing,
  onRefresh,
  onRetry,
}: AgentTheatreProps) {
  // The task whose live session is exploded into the theatre modal.
  const [watching, setWatching] = React.useState<GalleryTask | null>(null);

  // Tick once a second so the "running for" labels advance between polls.
  const [, force] = React.useReducer((n: number) => n + 1, 0);
  React.useEffect(() => {
    const t = setInterval(force, 1000);
    return () => clearInterval(t);
  }, []);

  const tasks = React.useMemo(() => roster ?? [], [roster]);

  // Keep the open theatre's task object fresh as the roster re-polls; if the
  // task drops out of the gallery (no longer RUNNING/watchable), surface that
  // in the modal rather than silently leaving a dead viewer mounted.
  const watchingLive = React.useMemo(
    () => (watching ? (tasks.find((t) => t.id === watching.id) ?? null) : null),
    [watching, tasks]
  );
  const watchingGone = watching !== null && watchingLive === null && !loading;

  if (!hasOrg) {
    return (
      <EmptyState
        icon={<BotIcon className="size-5" />}
        title="No active organization"
        description="Pick an organization from the sidebar to watch its running agents."
      />
    );
  }

  if (loading && tasks.length === 0) {
    return (
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="aspect-video w-full rounded-lg" />
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div className="space-y-3">
        <div className="text-destructive bg-destructive/10 border-destructive/20 rounded-md border p-4 text-sm">
          Couldn&apos;t load the agent theatre: {error}
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          <RefreshCwIcon className="size-3.5" />
          Retry
        </Button>
      </div>
    );
  }

  if (tasks.length === 0) {
    return (
      <EmptyState
        icon={<MonitorPlayIcon className="size-5" />}
        title="No agents to watch"
        description="Running agents launched on a VNC-capable environment appear here as live snapshot tiles you can explode into a full session. Dispatch a watchable agent to populate the theatre."
        actionHref="/agents"
        actionLabel="Go to Agents"
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-muted-foreground text-sm">
          {tasks.length} live {tasks.length === 1 ? "agent" : "agents"} · snapshots refresh every{" "}
          {GALLERY_POLL_MS / 1000}s
        </p>
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
          <RefreshCwIcon className={cn("size-3.5", refreshing && "animate-spin")} />
          Refresh
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {tasks.map((task) => (
          <SnapshotTile key={task.id} task={task} onWatch={() => setWatching(task)} />
        ))}
      </div>

      {/* Explode → theatre. Near-fullscreen modal hosting the live session. */}
      <Dialog
        open={watching !== null}
        onOpenChange={(open) => {
          if (!open) setWatching(null);
        }}
      >
        <DialogContent className="h-[92vh] w-[96vw] max-w-[96vw] gap-3 sm:max-w-[96vw]">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <MonitorPlayIcon className="size-4" />
              Live agent session
            </DialogTitle>
            <DialogDescription className="flex items-center gap-2 font-mono text-xs">
              <span>{watching?.id}</span>
              {watching && (
                <a
                  href={`/agents/runs/${watching.id}/vnc`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 underline-offset-2 hover:underline"
                >
                  <ExpandIcon className="size-3" />
                  Pop out
                </a>
              )}
            </DialogDescription>
          </DialogHeader>
          {watchingGone ? (
            <div className="flex flex-1 items-center justify-center">
              <EmptyState
                icon={<BotIcon className="size-5" />}
                title="Session ended"
                description="This agent is no longer running, so its live session has closed."
              />
            </div>
          ) : (
            watching && <VncViewer vncPath={watching.vncUrl} className="flex-1" />
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * A single gallery tile: the agent's latest framebuffer snapshot with a
 * status overlay and a "Watch live" affordance. Falls back to a graceful
 * placeholder before the first frame arrives or if the snapshot 404s.
 */
function SnapshotTile({ task, onWatch }: { task: GalleryTask; onWatch: () => void }) {
  // Track the specific snapshot URL that failed to load rather than a bare
  // boolean, so a freshly presigned URL on the next poll automatically
  // recovers (it differs from the errored one) without a state-reset effect.
  // A transient 404 (frame not written yet) therefore self-heals.
  const [erroredUrl, setErroredUrl] = React.useState<string | null>(null);

  const hasFrame = Boolean(task.snapshotUrl) && task.snapshotUrl !== erroredUrl;

  return (
    <Card className="overflow-hidden p-0">
      <button
        type="button"
        onClick={onWatch}
        className="group focus-visible:ring-ring relative block w-full text-left focus-visible:ring-2 focus-visible:outline-none"
        aria-label={`Watch live session for agent ${shortId(task.id)}`}
      >
        <div className="bg-muted relative aspect-video w-full overflow-hidden">
          {hasFrame ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={task.snapshotUrl as string}
              alt={`Latest snapshot of agent ${shortId(task.id)}`}
              className="h-full w-full object-cover"
              onError={() => setErroredUrl(task.snapshotUrl)}
            />
          ) : (
            <div className="text-muted-foreground flex h-full w-full flex-col items-center justify-center gap-2">
              <Loader2Icon className="size-5 animate-spin" />
              <span className="text-xs">Waiting for first frame…</span>
            </div>
          )}

          {/* Hover scrim + explode affordance. */}
          <div className="absolute inset-0 flex items-center justify-center bg-black/0 opacity-0 transition group-hover:bg-black/40 group-hover:opacity-100">
            <span className="inline-flex items-center gap-1.5 rounded-md bg-black/70 px-3 py-1.5 text-sm font-medium text-white">
              <MonitorPlayIcon className="size-4" />
              Watch live
            </span>
          </div>

          {/* Live badge — pulsing dot top-left. */}
          <div className="absolute top-2 left-2">
            <Badge variant="default" className="gap-1.5">
              <span className="relative flex size-2">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-75" />
                <span className="relative inline-flex size-2 rounded-full bg-current" />
              </span>
              {task.status}
            </Badge>
          </div>
        </div>
      </button>

      <CardContent className="flex items-center justify-between gap-2 p-3">
        <div className="min-w-0">
          <p className="truncate font-mono text-xs font-medium" title={task.id}>
            {shortId(task.id)}
          </p>
          <p className="text-muted-foreground text-xs">
            running for {elapsedLabel(task.startedAt)}
          </p>
        </div>
        <Button size="sm" variant="outline" onClick={onWatch}>
          <MonitorPlayIcon className="size-4" />
          Watch
        </Button>
      </CardContent>
    </Card>
  );
}
