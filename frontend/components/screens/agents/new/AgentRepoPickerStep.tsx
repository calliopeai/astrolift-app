"use client";

import { BotIcon, ChevronDownIcon, CogIcon, LinkIcon, PlugIcon } from "lucide-react";
import Link from "next/link";
import * as React from "react";

import { ScmEmptyConnectAction } from "@/components/ScmConnectPrompt";
import type { UseScmConnect } from "@/components/use-scm-connect";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import type { AstroliftRemoteRepoList, AstroliftSourceConnection } from "@/graphql/scm/scm.types";
import { cn } from "@/lib/utils";
import { SourceRepositoryPicker } from "@/components/wizard/SourceRepositoryPicker";

/** Everything agent wizard step 1 renders; built by useAgentRepoPicker. */
export interface AgentRepoPickerStepViewProps {
  connectionsLoading: boolean;
  /** Active, non-OAuth-app-config source connections. */
  connections: AstroliftSourceConnection[];
  connectionId: string;
  sourceRepo: string;
  /** The branch / ref the discovery step scans. */
  refValue: string;
  onConnectionChange: (connectionId: string) => void;
  reposLoading: boolean;
  repoList: AstroliftRemoteRepoList | undefined;
  search: string;
  onSearchChange: (search: string) => void;
  onPickRepo: (fullName: string) => void;
  onRefChange: (ref: string) => void;
  /** Registered-agent count per repo full name (the "already registered" badge). */
  repoToAgentCount: Map<string, number>;
  scm: UseScmConnect;
}

/** Agent wizard step 1: pick a source connection, a repository, and the ref to scan. */
export function AgentRepoPickerStepView({
  connectionsLoading,
  connections: usable,
  connectionId,
  sourceRepo,
  refValue,
  onConnectionChange,
  reposLoading,
  repoList,
  search,
  onSearchChange,
  onPickRepo,
  onRefChange,
  repoToAgentCount,
  scm,
}: AgentRepoPickerStepViewProps) {
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
          can list your repositories and scan them for agent manifests.
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
        repoCounts={repoToAgentCount}
        registeredKind="agent"
        registeredNote={(count) =>
          `This repo already hosts ${count} registered agent${count === 1 ? "" : "s"}. Re-scanning adds only newly-added agents.`
        }
        scm={scm}
        missingHostLink={
          <Link
            href="/providers#source"
            className="text-primary text-xs underline-offset-4 hover:underline"
          >
            Missing a host? Connect another source →
          </Link>
        }
        emptyReposLink={
          <Link href="/providers#source" className="underline">
            /providers#source
          </Link>
        }
        pickedDetails={
          sourceRepo && (
            <div className="space-y-2">
              <p className="text-muted-foreground text-xs">
                Picked <code className="bg-muted rounded px-1 py-0.5 font-mono">{sourceRepo}</code>.
              </p>
              <div className="space-y-1">
                <Label htmlFor="agent-ref">Branch / ref to scan</Label>
                <input
                  id="agent-ref"
                  value={refValue}
                  onChange={(e) => onRefChange(e.target.value)}
                  className="border-input bg-background ring-offset-background focus-visible:ring-ring flex h-9 w-full rounded-md border px-3 py-1 font-mono text-xs shadow-xs transition-colors focus-visible:ring-1 focus-visible:outline-none"
                />
                <p className="text-muted-foreground text-xs">
                  The branch the next step scans for{" "}
                  <code className="font-mono">agents/*/astrolift.toml</code> and a root{" "}
                  <code className="font-mono">astrolift.toml</code>. Defaults to the
                  repository&apos;s default branch.
                </p>
              </div>
            </div>
          )
        }
      />
    </div>
  );
}

/**
 * Collapsible orientation card shown above the first step body. Mirrors the
 * register-app wizard's intro, retargeted to agent discovery.
 */
function IntroCard() {
  const [open, setOpen] = React.useState(true);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="bg-muted/30 rounded-md border">
      <CollapsibleTrigger className="hover:bg-muted/50 flex w-full items-center justify-between gap-3 rounded-md px-4 py-3 text-left transition-colors">
        <div className="flex flex-col">
          <span className="text-sm font-medium">How agent registration works</span>
          <span className="text-muted-foreground text-xs">
            Three quick steps: connect a Git source, scan the repo for agent manifests, then
            register the agents you discover.
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
            title="Connect"
            description="Pick a source connection and the repo that holds your agent manifests."
          />
          <IntroTile
            icon={BotIcon}
            title="Discover"
            description="We scan agents/*/astrolift.toml and the root manifest, then list every agent we find."
          />
          <IntroTile
            icon={CogIcon}
            title="Register"
            description="Pick a project and register the repo's agents — each becomes an agent workload on the Agents list."
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
