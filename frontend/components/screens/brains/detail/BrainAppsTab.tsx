"use client";

import type { TemporaryAppPolicyState } from "../../apps/temporary-app-model";

import { AppWindowIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import type { ListStateController } from "@/components/list/list-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";

import {
  HostBrainAppDialog,
  type HostBrainAppProject,
  type HostBrainAppValues,
} from "../HostBrainAppDialog";
import type { HostedAppRow } from "./brain-detail-lists";

export interface BrainAppsTabProps {
  slug: string;
  /** Hosting reads the layer's MCP URLs, so it needs an environment with a URL. */
  served: boolean;
  /** `app.create`: the dialog picks the project, which the server checks. */
  canHost: boolean;
  list: ListStateController;
  rows: HostedAppRow[];
  totalCount: number;
  hosting: boolean;
  onHost: () => void;
  onDismissHost: () => void;
  hostProjects: HostBrainAppProject[];
  hostSubmitting: boolean;
  temporary?: TemporaryAppPolicyState;
  onSubmitHost: (values: HostBrainAppValues) => void;
}

// The row's link is an ::after overlay across the row; a link in a cell
// has to sit above it to be reachable.
const ABOVE_ROW_LINK = "relative z-10";

/**
 * Apps (#2243, #2427): the apps hosted on this layer that the viewer can
 * read, each with the environments it runs in, and Host an app, which
 * creates one from the brain-app template; every deploy hands it the
 * layer's MCP URLs and a read token. Pure.
 */
export function BrainAppsTab({
  slug,
  served,
  canHost,
  list,
  rows,
  totalCount,
  hosting,
  onHost,
  onDismissHost,
  hostProjects,
  hostSubmitting,
  temporary,
  onSubmitHost,
}: BrainAppsTabProps) {
  const t = useTranslations("brains.apps");
  const columns: Column<HostedAppRow>[] = [
    {
      id: "app",
      header: t("app"),
      sortKey: "app",
      cell: (row) => <span className="font-mono">{row.slug}</span>,
    },
    {
      id: "environments",
      header: t("environments"),
      cell: (row) =>
        row.environments.length ? (
          <span className="flex flex-wrap gap-1">
            {row.environments.map((env) => (
              <Badge key={env} variant="secondary" className="font-mono">
                {env}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-muted-foreground text-xs">{t("noEnvironments")}</span>
        ),
    },
    {
      id: "open",
      header: <span className="sr-only">{t("open")}</span>,
      align: "right",
      cellClassName: ABOVE_ROW_LINK,
      cell: (row) => (
        <Link
          href={`/apps/${encodeURIComponent(row.slug)}/deployments`}
          className="text-primary text-xs hover:underline"
        >
          {t("deployments")}
        </Link>
      ),
    },
  ];
  return (
    <Section
      title={t("title")}
      description={t("description")}
      action={
        canHost ? (
          <Button
            size="sm"
            onClick={onHost}
            disabled={!served}
            title={served ? undefined : t("notServed")}
          >
            <AppWindowIcon className="size-4" />
            {t("host")}
          </Button>
        ) : undefined
      }
    >
      <ListPage<HostedAppRow>
        embedded
        list={list}
        label={t("title")}
        columns={columns}
        rows={rows}
        getRowId={(row) => row.slug}
        rowHref={(row) => `/apps/${encodeURIComponent(row.slug)}`}
        totalCount={totalCount}
        empty={{
          icon: <AppWindowIcon className="size-5" />,
          title: t("emptyTitle"),
          description: served ? t("emptyDescription") : t("emptyNotServed"),
        }}
      />
      <HostBrainAppDialog
        brainSlug={hosting ? slug : null}
        projects={hostProjects}
        submitting={hostSubmitting}
        temporary={temporary}
        onSubmit={onSubmitHost}
        onDismiss={onDismissHost}
      />
    </Section>
  );
}
