"use client";

import { ChevronDownIcon, CogIcon, LinkIcon, PlugIcon, RocketIcon, ServerIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { EmptyState } from "@/components/EmptyState";
import { ScmEmptyConnectAction } from "@/components/ScmConnectPrompt";
import type { UseScmConnect } from "@/components/use-scm-connect";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRemoteRepoList, AstroliftSourceConnection } from "@/graphql/scm/scm.types";
import { cn } from "@/lib/utils";
import { SourceRepositoryPicker } from "@/components/wizard/SourceRepositoryPicker";

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

  return (
    <div className="flex flex-col gap-5">
      <IntroCard />

      <SourceRepositoryPicker
        connections={usable}
        connectionId={connectionId}
        sourceRepo={sourceRepo}
        onConnectionChange={onConnectionChange}
        reposLoading={reposLoading}
        repoList={repoList}
        search={search}
        onSearchChange={onSearchChange}
        onPickRepo={onPickRepo}
        repoCounts={repoToAppCount}
        registeredKind="app"
        registeredNote={(count) =>
          `This repo already hosts ${count} registered app${count === 1 ? "" : "s"} (different manifest path).`
        }
        scm={scm}
        missingHostLink={
          <Link
            href="/providers#source"
            target="_blank"
            rel="noreferrer"
            className="text-primary text-xs underline-offset-4 hover:underline"
          >
            Missing a host? Connect another source →
          </Link>
        }
        emptyReposLink={
          <Link href="/providers#source" target="_blank" rel="noreferrer" className="underline">
            /providers
          </Link>
        }
        pickedDetails={
          sourceRepo && (
            <p className="text-muted-foreground text-xs">
              Picked <code className="bg-muted rounded px-1 py-0.5 font-mono">{sourceRepo}</code> on{" "}
              <code className="bg-muted rounded px-1 py-0.5 font-mono">{defaultBranch}</code>.
            </p>
          )
        }
      />
    </div>
  );
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
