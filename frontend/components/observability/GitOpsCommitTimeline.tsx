"use client";

import { ExternalLinkIcon, GitCommitIcon } from "lucide-react";

import { Feed } from "@/components/feed/Feed";
import { Button } from "@/components/ui/button";

import type { GitOpsCommit } from "./types";

export interface GitOpsCommitTimelineProps {
  commits: GitOpsCommit[];
  /** Empty-state copy. Defaults to "No config-repo commits yet." */
  emptyMessage?: string;
  /** The caller's cursor: older commits load as the reader nears the end. */
  hasMore?: boolean;
  loadingMore?: boolean;
  onLoadMore?: () => void;
  loading?: boolean;
  error?: string | null;
  onRetry?: () => void;
  /** Height class for the frame; defaults to the Feed's. */
  maxHeight?: string;
  className?: string;
}

/**
 * Config-repo commits, threaded into a deployment timeline. Spec 07 §12.
 * A timeline grows, so it is a Feed (list rule 5): it scrolls in its own
 * frame, grouped by day, and loads older commits on the caller's cursor.
 *
 * Each line shows the short hash, the commit message, the author, the time,
 * and links to the config-repo diff and the source-repo commit that
 * triggered this rollout.
 */
export function GitOpsCommitTimeline({
  commits,
  emptyMessage = "No config-repo commits yet.",
  hasMore,
  loadingMore,
  onLoadMore,
  loading,
  error,
  onRetry,
  maxHeight,
  className,
}: GitOpsCommitTimelineProps) {
  return (
    <Feed<GitOpsCommit>
      label="Config-repo commits"
      items={commits}
      keyOf={(c) => c.hash}
      groupBy={{ day: (c) => c.occurredAt }}
      hasMore={hasMore}
      loadingMore={loadingMore}
      onLoadMore={onLoadMore}
      loading={loading}
      error={error}
      onRetry={onRetry}
      maxHeight={maxHeight}
      className={className}
      empty={{ icon: <GitCommitIcon className="size-5" />, title: emptyMessage }}
      renderItem={(commit) => (
        <div className="flex min-w-0 items-start gap-3 px-1">
          <span className="bg-card mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border">
            <GitCommitIcon className="text-muted-foreground size-3" />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex min-w-0 flex-wrap items-baseline gap-2">
              <code className="bg-muted rounded px-1.5 py-0.5 font-mono text-xs">
                {commit.hash.slice(0, 8)}
              </code>
              <span className="min-w-0 text-sm [overflow-wrap:anywhere]">{commit.message}</span>
            </div>
            <div className="text-muted-foreground mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs">
              <span className="min-w-0 [overflow-wrap:anywhere]">{commit.author}</span>
              <time dateTime={commit.occurredAt} className="font-mono">
                {new Date(commit.occurredAt).toLocaleString()}
              </time>
              {commit.workflowRunId && (
                <span className="font-mono">run {commit.workflowRunId.slice(0, 12)}…</span>
              )}
            </div>
            {(commit.diffUrl || commit.sourceCommitUrl) && (
              <div className="mt-2 flex flex-wrap gap-2">
                {commit.diffUrl && (
                  <Button asChild variant="outline" size="sm" className="h-7 text-xs">
                    <a href={commit.diffUrl} target="_blank" rel="noreferrer">
                      <ExternalLinkIcon className="size-3" />
                      Diff
                    </a>
                  </Button>
                )}
                {commit.sourceCommitUrl && (
                  <Button asChild variant="outline" size="sm" className="h-7 text-xs">
                    <a href={commit.sourceCommitUrl} target="_blank" rel="noreferrer">
                      <ExternalLinkIcon className="size-3" />
                      Source
                    </a>
                  </Button>
                )}
              </div>
            )}
          </div>
        </div>
      )}
    />
  );
}
