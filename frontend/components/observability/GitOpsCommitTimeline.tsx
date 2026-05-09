"use client";

import { ExternalLinkIcon, GitCommitIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import type { GitOpsCommit } from "./types";

export interface GitOpsCommitTimelineProps {
  commits: GitOpsCommit[];
  /** Empty-state copy. Defaults to "No config-repo commits yet." */
  emptyMessage?: string;
  className?: string;
}

/**
 * Vertical list of config-repo commits, threaded into a deployment
 * timeline. Spec 07 §12.
 *
 * Each row shows the short hash, the commit message (truncated to a
 * line), the author, the relative time, and links to the config-repo
 * diff and the source-repo commit that triggered this rollout.
 */
export function GitOpsCommitTimeline({
  commits,
  emptyMessage = "No config-repo commits yet.",
  className,
}: GitOpsCommitTimelineProps) {
  if (commits.length === 0) {
    return (
      <div
        className={cn(
          "rounded-md border border-dashed bg-card p-6 text-center text-sm text-muted-foreground",
          className,
        )}
      >
        {emptyMessage}
      </div>
    );
  }

  return (
    <ol className={cn("relative space-y-4 border-l pl-6", className)}>
      {commits.map((commit) => (
        <li key={commit.hash} className="relative">
          <span className="absolute -left-[31px] flex size-6 items-center justify-center rounded-full border bg-card">
            <GitCommitIcon className="size-3 text-muted-foreground" />
          </span>
          <div className="flex flex-wrap items-baseline gap-2">
            <code className="rounded bg-muted px-1.5 py-0.5 text-xs">
              {commit.hash.slice(0, 8)}
            </code>
            <span className="truncate text-sm">{commit.message}</span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
            <span>{commit.author}</span>
            <span>{new Date(commit.occurredAt).toLocaleString()}</span>
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
        </li>
      ))}
    </ol>
  );
}
