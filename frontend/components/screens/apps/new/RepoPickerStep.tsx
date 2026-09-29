"use client";

import {
  ChevronDownIcon,
  CogIcon,
  GitBranchIcon,
  LinkIcon,
  LockIcon,
  PlugIcon,
  RocketIcon,
  ServerIcon,
  UnlockIcon,
} from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ScmEmptyConnectAction, ScmReauthAction } from "@/components/ScmConnectPrompt";
import type { UseScmConnect } from "@/components/use-scm-connect";
import { Badge } from "@/components/ui/badge";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import {
  Combobox,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
} from "@/components/ui/combobox";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftRemoteRepoList,
  AstroliftSourceConnection,
  ScmConnectionKind,
} from "@/graphql/scm/scm.types";
import { cn } from "@/lib/utils";

/** Everything wizard step 1 renders; built by useRepoPicker. */
export interface RepoPickerStepViewProps {
  /** Cluster-count preflight still loading. */
  clusterLoading: boolean;
  /** The org has no managed cluster (#315/#316): the step is gated. */
  noCluster: boolean;
  connectionsLoading: boolean;
  /** Active, non-OAuth-app-config source connections. */
  connections: AstroliftSourceConnection[];
  connectionId: string;
  sourceRepo: string;
  defaultBranch: string;
  onConnectionChange: (connectionId: string) => void;
  reposLoading: boolean;
  repoList: AstroliftRemoteRepoList | undefined;
  search: string;
  onSearchChange: (search: string) => void;
  onPickRepo: (fullName: string) => void;
  /** Registered-app count per repo full name (the monorepo badge). */
  repoToAppCount: Map<string, number>;
  scm: UseScmConnect;
}

/** Wizard step 1: pick a source connection and a repository. */
export function RepoPickerStepView({
  clusterLoading,
  noCluster,
  connectionsLoading,
  connections: usable,
  connectionId,
  sourceRepo,
  defaultBranch,
  onConnectionChange,
  reposLoading,
  repoList,
  search,
  onSearchChange,
  onPickRepo,
  repoToAppCount,
  scm,
}: RepoPickerStepViewProps) {
  if (clusterLoading) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (noCluster) {
    return (
      <EmptyState
        icon={<ServerIcon className="size-5" />}
        title="No managed clusters yet"
        description="Astrolift deploys apps to Kubernetes clusters that have been brought into management (#316). Connect a cluster, install prereqs, then click Bring into management."
        actionHref="/clusters"
        actionLabel="Manage clusters"
      />
    );
  }

  if (connectionsLoading) {
    return (
      <div className="flex flex-col gap-2">
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    );
  }

  if (usable.length === 0) {
    return (
      <div className="border-muted-foreground/30 bg-muted/30 flex flex-col items-start gap-3 rounded-md border border-dashed p-6">
        <div className="flex items-center gap-2">
          <LinkIcon className="text-muted-foreground size-5" />
          <p className="font-medium">No source connections yet</p>
        </div>
        <p className="text-muted-foreground text-sm">
          Connect a Git host (GitHub App, GitLab OAuth, or a personal-access token) so the platform
          can list your repositories and watch them for pushes.
        </p>
        <ScmEmptyConnectAction scm={scm} />
      </div>
    );
  }

  const pickedConnection = usable.find((c) => c.id === connectionId);

  return (
    <div className="flex flex-col gap-5">
      <IntroCard />

      <div className="space-y-2">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="connection">Source connection</Label>
            {pickedConnection && (
              <Badge variant="outline" className="text-2xs font-normal">
                {connectionKindLabel(pickedConnection.kind as ScmConnectionKind)}
              </Badge>
            )}
          </div>
          <Link
            href="/providers#source"
            target="_blank"
            rel="noreferrer"
            className="text-primary text-xs underline-offset-4 hover:underline"
          >
            Missing a host? Connect another source →
          </Link>
        </div>
        <Select value={connectionId} onValueChange={(v) => onConnectionChange(v)}>
          <SelectTrigger id="connection">
            <SelectValue placeholder="Choose a connection" />
          </SelectTrigger>
          <SelectContent>
            {usable.map((c) => (
              <SelectItem key={c.id} value={c.id}>
                <span className="font-medium">{c.displayName || c.name}</span>{" "}
                <span className="text-muted-foreground text-xs">
                  ({connectionKindLabel(c.kind as ScmConnectionKind)})
                </span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {connectionId && (
        <div className="space-y-2">
          <Label htmlFor="repo-picker">Repository</Label>
          {reposLoading ? (
            <Skeleton className="h-10 w-full" />
          ) : repoList?.errorCode ? (
            <div className="text-destructive flex flex-col items-start gap-1 text-xs">
              <span>{repoList.errorMessage ?? repoList.errorCode}</span>
              {repoList.recoverable && (
                <ScmReauthAction connectionKind={pickedConnection?.kind ?? ""} scm={scm} />
              )}
            </div>
          ) : repoList && repoList.repos.length > 0 ? (
            <>
              <Combobox
                items={repoList.repos}
                itemToStringLabel={(r) => (r as { fullName: string }).fullName}
                value={
                  sourceRepo
                    ? (repoList.repos.find((r) => r.fullName === sourceRepo) ?? null)
                    : null
                }
                onValueChange={(v) => {
                  if (v && typeof v === "object" && "fullName" in v) {
                    onPickRepo((v as { fullName: string }).fullName);
                  }
                }}
                inputValue={search}
                onInputValueChange={(v) => onSearchChange(v ?? "")}
              >
                <ComboboxInput
                  placeholder={`Search ${repoList.repos.length} repo${
                    repoList.repos.length === 1 ? "" : "s"
                  }…`}
                />
                <ComboboxContent>
                  <ComboboxEmpty>No matching repos.</ComboboxEmpty>
                  <ComboboxList>
                    {(item) => {
                      const r = item as {
                        fullName: string;
                        name: string;
                        visibility: string;
                        defaultBranch: string | null;
                        isFork: boolean;
                        isArchived: boolean;
                        pushedAt: string | null;
                      };
                      const existingCount = repoToAppCount.get(r.fullName) ?? 0;
                      return (
                        <ComboboxItem key={r.fullName} value={r}>
                          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
                            <div className="flex min-w-0 items-center gap-2">
                              <span className="truncate font-mono text-xs">{r.fullName}</span>
                              {existingCount > 0 && (
                                <Badge
                                  variant="secondary"
                                  className="text-2xs shrink-0 gap-1 px-1 py-0"
                                  title={`This repo already hosts ${existingCount} registered app${existingCount === 1 ? "" : "s"} (different manifest path).`}
                                >
                                  {existingCount === 1
                                    ? "1 app already registered"
                                    : `${existingCount} apps already registered`}
                                </Badge>
                              )}
                            </div>
                            <div className="text-muted-foreground text-2xs flex items-center gap-2">
                              <VisibilityBadge visibility={r.visibility} />
                              {r.defaultBranch && (
                                <span className="inline-flex items-center gap-1">
                                  <GitBranchIcon className="size-3" />
                                  {r.defaultBranch}
                                </span>
                              )}
                              {r.isFork && <span>fork</span>}
                              {r.isArchived && <span>archived</span>}
                              {r.pushedAt && <span>updated {formatRelative(r.pushedAt)}</span>}
                            </div>
                          </div>
                        </ComboboxItem>
                      );
                    }}
                  </ComboboxList>
                </ComboboxContent>
              </Combobox>
              {sourceRepo && (
                <p className="text-muted-foreground text-xs">
                  Picked{" "}
                  <code className="bg-muted rounded px-1 py-0.5 font-mono">{sourceRepo}</code> on{" "}
                  <code className="bg-muted rounded px-1 py-0.5 font-mono">{defaultBranch}</code>.
                </p>
              )}
            </>
          ) : (
            <p className="text-muted-foreground text-xs">
              No repos visible to this connection. Adjust visibility scopes on{" "}
              <Link href="/providers#source" target="_blank" rel="noreferrer" className="underline">
                /providers
              </Link>
              .
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function connectionKindLabel(kind: ScmConnectionKind): string {
  switch (kind) {
    case "github_app_install":
      return "GitHub App";
    case "github_oauth_user":
      return "GitHub OAuth";
    case "github_pat":
      return "GitHub PAT";
    case "gitlab_oauth_user":
      return "GitLab OAuth";
    case "gitlab_pat":
      return "GitLab PAT";
    case "bitbucket_oauth_user":
      return "Bitbucket OAuth";
    case "bitbucket_pat":
      return "Bitbucket PAT";
    case "gitea_oauth_user":
      return "Gitea OAuth";
    case "gitea_pat":
      return "Gitea PAT";
    default:
      return kind;
  }
}

function VisibilityBadge({ visibility }: { visibility: string }) {
  if (visibility === "private" || visibility === "internal") {
    return (
      <Badge variant="secondary" className="text-2xs gap-1 px-1 py-0">
        <LockIcon className="size-2.5" /> {visibility}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-2xs gap-1 px-1 py-0">
      <UnlockIcon className="size-2.5" /> {visibility}
    </Badge>
  );
}

function formatRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const days = Math.floor(diff / (1000 * 60 * 60 * 24));
  if (days === 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 30) return `${days}d ago`;
  if (days < 365) return `${Math.floor(days / 30)}mo ago`;
  return `${Math.floor(days / 365)}y ago`;
}

/**
 * Collapsible orientation card shown above the first step body. Three
 * icon tiles surface the wizard's structure before the operator starts
 * filling in fields — a friendlier landing than "pick a connection"
 * cold.
 */
function IntroCard() {
  const [open, setOpen] = React.useState(true);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="bg-muted/30 rounded-md border">
      <CollapsibleTrigger className="hover:bg-muted/50 flex w-full items-center justify-between gap-3 rounded-md px-4 py-3 text-left transition-colors">
        <div className="flex flex-col">
          <span className="text-sm font-medium">How registration works</span>
          <span className="text-muted-foreground text-xs">
            Three steps: Source (the repo and its manifest), Run (the app and how it deploys), then
            Review.
          </span>
        </div>
        <ChevronDownIcon
          className={cn(
            "text-muted-foreground size-4 shrink-0 transition-transform",
            open && "rotate-180"
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="grid gap-3 px-4 pb-4 md:grid-cols-3">
          <IntroTile
            icon={PlugIcon}
            title="1 Source"
            description="Pick a source connection and repo; we load or scaffold its astrolift.toml manifest."
          />
          <IntroTile
            icon={CogIcon}
            title="2 Run"
            description="Name the app, pick its project, and choose a trigger: auto on push, cron, or manual."
          />
          <IntroTile
            icon={RocketIcon}
            title="3 Review"
            description="See what will be created and what will deploy, then create the app."
          />
        </div>
      </CollapsibleContent>
    </Collapsible>
  );
}

function IntroTile({
  icon: Icon,
  title,
  description,
}: {
  icon: typeof PlugIcon;
  title: string;
  description: string;
}) {
  return (
    <div className="bg-background flex flex-col gap-2 rounded-md border p-3">
      <div className="bg-primary/10 text-primary flex size-7 items-center justify-center rounded-md">
        <Icon className="size-4" />
      </div>
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="text-muted-foreground mt-0.5 text-xs leading-relaxed">{description}</p>
      </div>
    </div>
  );
}
