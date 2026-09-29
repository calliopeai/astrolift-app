"use client";

import { UsersIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { Panel, type PanelSpan } from "@/components/panel/Panel";

import type { useOwnership } from "./use-ownership";

export type OwnershipPanelProps = ReturnType<typeof useOwnership> & {
  /** From the app record: empty when the app is in no project. */
  projectName: string;
  /** The home team, from the app record. */
  teamName: string;
  /** Where project and team grants are changed (the Settings tab). */
  settingsHref: string;
  span?: PanelSpan;
};

/**
 * Who owns the app: its project, its home team and the teams granted access
 * with their level. Reading only; assigning a project and granting teams
 * stay in Settings, which the heading links to.
 */
export function OwnershipPanel({
  projectName,
  teamName,
  accesses,
  loading,
  error,
  onRetry,
  settingsHref,
  span = 4,
}: OwnershipPanelProps) {
  const t = useTranslations("apps.overview.ownership");
  const granted = accesses.filter((a) => !a.isHome);
  return (
    <Panel
      title={t("title")}
      icon={<UsersIcon className="size-4" />}
      span={span}
      loading={loading}
      error={error}
      onRetry={onRetry}
      actions={
        <Link href={settingsHref} className="text-primary text-xs hover:underline">
          {t("manage")}
        </Link>
      }
    >
      <dl className="grid min-w-0 grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
        <dt className="text-muted-foreground text-xs">{t("project")}</dt>
        <dd className="min-w-0 [overflow-wrap:anywhere]">
          {projectName || <span className="text-muted-foreground">{t("noProject")}</span>}
        </dd>
        <dt className="text-muted-foreground text-xs">{t("homeTeam")}</dt>
        <dd className="min-w-0 [overflow-wrap:anywhere]">
          {teamName || <span className="text-muted-foreground">{t("noTeam")}</span>}
        </dd>
        <dt className="text-muted-foreground text-xs">{t("granted")}</dt>
        <dd className="min-w-0">
          {granted.length === 0 ? (
            <span className="text-muted-foreground">{t("noGrants")}</span>
          ) : (
            <ul className="space-y-1">
              {granted.map((a) => (
                <li key={a.id} className="flex min-w-0 flex-wrap items-baseline gap-x-2">
                  <span className="min-w-0 [overflow-wrap:anywhere]">
                    {a.teamName || a.teamSlug}
                  </span>
                  <span className="text-muted-foreground font-mono text-xs">{a.accessLevel}</span>
                </li>
              ))}
            </ul>
          )}
        </dd>
      </dl>
    </Panel>
  );
}
