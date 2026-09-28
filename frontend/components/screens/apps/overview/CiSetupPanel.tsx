"use client";

import { WorkflowIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import type * as React from "react";

import { Panel, type PanelSpan } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";
import { useFormatters } from "@/lib/i18n/formatters";

import { DRIFT_BADGE } from "./ci-setup-meta";

export type CiSetupApp = Pick<
  AstroliftRegisteredApp,
  | "sourceKind"
  | "sourceRepo"
  | "deployBranch"
  | "defaultBranch"
  | "autowire"
  | "sourceWebhookInstalledAt"
  | "ciWorkflowSyncStatus"
>;

export interface CiSetupPanelProps {
  app: CiSetupApp;
  /** The full CI setup (secrets, workflow push and pull) in Settings. */
  settingsHref: string;
  /** The deploy tokens the workflow authenticates with (Access › Tokens). */
  tokensHref: string;
  span?: PanelSpan;
}

type Dot = "ok" | "warn" | "error" | "muted";

const STEP_DOT: Record<string, Dot> = {
  ok: "ok",
  missing: "warn",
  error: "error",
  phantom: "warn",
};
const DRIFT_DOT: Record<string, Dot> = {
  in_sync: "ok",
  template_stale: "warn",
  repo_drift: "warn",
  conflict: "error",
  absent: "warn",
  unknown: "muted",
};

/**
 * How a push reaches a deploy, read from the app record alone: the repo and
 * branch, whether the CI workflow in the repo matches the platform's, the
 * webhook, and the deploy secret. Changing any of it is Settings' job.
 */
export function CiSetupPanel({ app, settingsHref, tokensHref, span = 4 }: CiSetupPanelProps) {
  const t = useTranslations("apps.overview.ci");
  const fmt = useFormatters();
  const aw = app.autowire ?? null;
  const drift = app.ciWorkflowSyncStatus?.state ?? null;
  const branch = app.deployBranch || app.defaultBranch;

  const rows: { key: string; label: string; dot: Dot; value: React.ReactNode }[] = [
    {
      key: "workflow",
      label: t("workflow"),
      dot: drift
        ? (DRIFT_DOT[drift] ?? "muted")
        : aw
          ? (STEP_DOT[aw.ciWorkflow] ?? "muted")
          : "muted",
      value: drift ? (DRIFT_BADGE[drift]?.label ?? drift) : (aw?.ciWorkflow ?? t("unknown")),
    },
    {
      key: "webhook",
      label: t("webhook"),
      dot: app.sourceWebhookInstalledAt ? "ok" : aw ? (STEP_DOT[aw.webhook] ?? "muted") : "warn",
      value: app.sourceWebhookInstalledAt ? (
        <span title={app.sourceWebhookInstalledAt}>
          {t("installed", { when: fmt.formatRelativeTime(app.sourceWebhookInstalledAt) })}
        </span>
      ) : (
        (aw?.webhook ?? t("notInstalled"))
      ),
    },
    {
      key: "secret",
      label: t("secret"),
      dot: aw ? (STEP_DOT[aw.secrets] ?? "muted") : "muted",
      value: aw?.secrets ?? t("unknown"),
    },
  ];

  return (
    <Panel
      title={t("title")}
      icon={<WorkflowIcon className="size-4" />}
      span={span}
      actions={
        app.sourceRepo ? (
          <Link href={settingsHref} className="text-primary text-xs hover:underline">
            {t("manage")}
          </Link>
        ) : undefined
      }
      empty={
        app.sourceRepo
          ? null
          : {
              icon: <WorkflowIcon className="size-5" />,
              title: t("emptyTitle"),
              description: t("emptyDescription"),
              actionHref: settingsHref,
              actionLabel: t("emptyAction"),
            }
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
        <p className="min-w-0 font-mono text-sm [overflow-wrap:anywhere]">
          {app.sourceRepo}
          {branch && <span className="text-muted-foreground"> @ {branch}</span>}
        </p>
        <ul className="space-y-1.5 text-sm">
          {rows.map((r) => (
            <li key={r.key} className="flex min-w-0 items-center gap-2">
              <StatusDot status={r.dot} />
              <span className="text-muted-foreground text-xs">{r.label}</span>
              <span className="min-w-0 font-mono text-xs [overflow-wrap:anywhere]">{r.value}</span>
            </li>
          ))}
        </ul>
        <Link href={tokensHref} className="text-primary self-start text-xs hover:underline">
          {t("tokens")}
        </Link>
      </div>
    </Panel>
  );
}
