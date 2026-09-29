"use client";

import { GitBranchIcon, GithubIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";

export interface RepoBadgeProps {
  sourceKind: string;
  sourceUrl: string | null | undefined;
  sourceRepo: string | null | undefined;
  branch: string | null | undefined;
}

/**
 * Compact "repo · branch" badge next to the app title (#408). Renders
 * only when `sourceUrl` is non-null; opens the repo in a new tab.
 * Uses the GitHub mark for GitHub sources and the generic git-branch
 * icon for everything else (GitLab, Bitbucket, Gitea, raw git URLs).
 *
 * The displayed repo prefers `sourceRepo` (e.g. "owner/name"), then
 * falls back to the URL pathname so we always show something
 * recognisable for self-hosted Git providers.
 */
export function RepoBadge({ sourceKind, sourceUrl, sourceRepo, branch }: RepoBadgeProps) {
  if (!sourceUrl) return null;

  const Icon = sourceKind === "github" ? GithubIcon : GitBranchIcon;

  const displayRepo = (sourceRepo && sourceRepo.trim()) || extractRepoFromUrl(sourceUrl);
  const displayBranch = branch?.trim() || null;

  return (
    <a
      href={sourceUrl}
      target="_blank"
      rel="noreferrer"
      className="focus-visible:ring-ring/50 inline-flex max-w-full min-w-0 rounded-md outline-none focus-visible:ring-2"
      aria-label={`Open ${displayRepo} repository in a new tab`}
    >
      <Badge
        variant="secondary"
        className="hover:bg-accent hover:text-foreground text-2xs max-w-full shrink gap-1.5 font-mono transition-colors"
      >
        <Icon className="size-3" aria-hidden />
        <span className="min-w-0 truncate" title={displayRepo}>
          {displayRepo}
        </span>
        {displayBranch ? (
          <>
            <span className="text-muted-foreground" aria-hidden>
              ·
            </span>
            <span className="min-w-0 truncate" title={displayBranch}>
              {displayBranch}
            </span>
          </>
        ) : null}
      </Badge>
    </a>
  );
}

function extractRepoFromUrl(url: string): string {
  try {
    const u = new URL(url);
    const trimmed = u.pathname.replace(/^\/+|\/+$|\.git$/g, "");
    return trimmed || u.hostname;
  } catch {
    return url;
  }
}
