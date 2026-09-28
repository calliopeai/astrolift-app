"use client";

import { CloudIcon, ExternalLinkIcon, PauseIcon, PlayIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import * as React from "react";

import { Can } from "@/components/Can";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import type { AstroliftAppEnvironment } from "@/graphql/lifecycle/lifecycle.types";

import type { EnvironmentsState } from "./use-environments";

export type EnvironmentsScreenProps = EnvironmentsState & {
  /** Set on an app's (or agent's) environments tab; unset on the global list. */
  appSlug?: string;
  /** The app's tab bar, rendered under the page header. */
  tabs?: React.ReactNode;
};

/**
 * Environments list, global or scoped to one app, with pause / resume
 * deploys. Data and mutations come from useEnvironments; this holds only
 * UI state (the environment awaiting pause confirmation).
 */
export function EnvironmentsScreen({
  appSlug,
  tabs,
  loading,
  environments: list,
  busy,
  canPause,
  onPause,
  onResume,
  onOpen,
}: EnvironmentsScreenProps) {
  const t = useTranslations("lists.environments");
  const [pauseTarget, setPauseTarget] = React.useState<AstroliftAppEnvironment | null>(null);

  // Row → detail navigation, but only from the global /environments list. On
  // the per-app environments tab the row stays inert: drilling into a global
  // environment detail would break out of the app shell + its sub-nav.
  function rowInteractiveProps(
    e: AstroliftAppEnvironment
  ): React.HTMLAttributes<HTMLTableRowElement> {
    if (appSlug) return {};
    const open = () => onOpen(e);
    return {
      tabIndex: 0,
      role: "link",
      "aria-label": `Open environment ${e.registeredAppSlug} · ${e.name}`,
      onClick: open,
      onKeyDown: (ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          open();
        }
      },
      className:
        "hover:bg-accent/30 focus-visible:outline-ring cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-[-2px]",
    };
  }

  return (
    <PageShell
      title={t("title")}
      description={
        appSlug ? (
          <span className="text-muted-foreground font-mono text-xs">
            {t("descriptionForApp", { slug: appSlug })}
          </span>
        ) : (
          t("description")
        )
      }
    >
      {tabs}
      <Card>
        <CardContent className="p-0">
          {loading && list.length === 0 ? (
            <div className="space-y-2 p-6">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : list.length === 0 ? (
            <div className="p-6">
              <EmptyState
                icon={<CloudIcon className="size-5" />}
                title={t("emptyTitle")}
                description={t("emptyDescription")}
                actionHref="/apps"
                actionLabel={t("openApps")}
              />
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t("columns.appEnv")}</TableHead>
                  <TableHead>{t("columns.url")}</TableHead>
                  <TableHead>{t("columns.cluster")}</TableHead>
                  <TableHead>{t("columns.approvals")}</TableHead>
                  <TableHead>{t("columns.status")}</TableHead>
                  <TableHead className="text-right">{t("columns.actions")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {list.map((e) => (
                  <TableRow key={e.id} {...rowInteractiveProps(e)}>
                    <TableCell>
                      <div className="font-medium">{e.registeredAppSlug}</div>
                      <div className="text-muted-foreground text-xs">
                        {t.rich("envName", {
                          name: () => <span className="font-mono">{e.name}</span>,
                        })}
                      </div>
                    </TableCell>
                    <TableCell>
                      {e.url ? (
                        <a
                          href={e.url}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(ev) => ev.stopPropagation()}
                          className="inline-flex items-center gap-1 text-sm hover:underline"
                        >
                          {e.url} <ExternalLinkIcon className="size-3" />
                        </a>
                      ) : (
                        <span className="text-muted-foreground text-sm">—</span>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs">{e.clusterSlug ?? "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{e.requiredApprovals}</TableCell>
                    <TableCell>
                      {e.deploysPaused ? (
                        <Badge variant="destructive" className="gap-1">
                          <PauseIcon className="size-3" /> {t("paused")}
                        </Badge>
                      ) : (
                        <Badge variant="secondary" className="gap-1">
                          <PlayIcon className="size-3" /> {t("active")}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right" onClick={(ev) => ev.stopPropagation()}>
                      {canPause &&
                        (e.deploysPaused ? (
                          <Can permission="app.deploy">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => onResume(e)}
                            >
                              <PlayIcon className="size-3" /> {t("resume")}
                            </Button>
                          </Can>
                        ) : (
                          <Can permission="app.deploy">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={busy}
                              onClick={() => setPauseTarget(e)}
                            >
                              <PauseIcon className="size-3" /> {t("pause")}
                            </Button>
                          </Can>
                        ))}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={pauseTarget !== null}
        onOpenChange={(next) => {
          if (!next) setPauseTarget(null);
        }}
        title={
          pauseTarget
            ? t("confirmPause.title", {
                app: pauseTarget.registeredAppSlug,
                env: pauseTarget.name,
              })
            : t("confirmPause.fallbackTitle")
        }
        description={t("confirmPause.description")}
        confirmLabel={t("confirmPause.confirm")}
        destructive
        onConfirm={async () => {
          if (!pauseTarget) return;
          await onPause(pauseTarget);
        }}
      />
    </PageShell>
  );
}
