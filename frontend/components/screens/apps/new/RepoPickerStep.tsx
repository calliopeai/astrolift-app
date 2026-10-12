"use client";

import { ChevronDownIcon, CogIcon, LinkIcon, PlugIcon, RocketIcon, ServerIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
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
  const t = useTranslations("appWizard.repo");
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
        title={t("noClusterTitle")}
        description={t("noClusterDescription")}
        actionHref="/clusters"
        actionLabel={t("manageClusters")}
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
          <p className="font-medium">{t("noConnectionsTitle")}</p>
        </div>
        <p className="text-muted-foreground text-sm">{t("noConnectionsDescription")}</p>
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
        registeredNote={(count) => t("registeredNote", { count })}
        scm={scm}
        missingHostLink={
          <Link
            href="/providers#source"
            target="_blank"
            rel="noreferrer"
            className="text-primary text-xs underline-offset-4 hover:underline"
          >
            {t("missingHost")}
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
              {t.rich("picked", {
                repo: sourceRepo,
                branch: defaultBranch,
                code: (chunks) => (
                  <code className="bg-muted rounded px-1 py-0.5 font-mono">{chunks}</code>
                ),
              })}
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
  const t = useTranslations("appWizard.repo");
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="bg-muted/30 rounded-md border">
      <CollapsibleTrigger className="hover:bg-muted/50 flex w-full items-center justify-between gap-3 rounded-md px-4 py-3 text-left transition-colors">
        <div className="flex flex-col">
          <span className="text-sm font-medium">{t("introTitle")}</span>
          <span className="text-muted-foreground text-xs">{t("introDescription")}</span>
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
            title={t("introSource")}
            description={t("introSourceDescription")}
          />
          <IntroTile icon={CogIcon} title={t("introRun")} description={t("introRunDescription")} />
          <IntroTile
            icon={RocketIcon}
            title={t("introReview")}
            description={t("introReviewDescription")}
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
