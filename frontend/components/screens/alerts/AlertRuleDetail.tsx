"use client";

import { useTranslations } from "next-intl";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { Badge } from "@/components/ui/badge";
import { QueryError } from "@/components/QueryError";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { prettyJson } from "./alert-format";
import type { useAlertRuleDetail } from "./use-alert-rule-detail";

const SEVERITY_TONE: Record<string, Dot> = {
  info: "ok",
  warn: "warn",
  warning: "warn",
  critical: "error",
  error: "error",
};

export type AlertRuleDetailProps = Omit<
  ReturnType<typeof useAlertRuleDetail>,
  "error" | "onRetry"
> & { error?: string | null; onRetry?: () => void };

/** Alert rule detail (#1106): state, target, mute, and the raw predicate/channels. */
export function AlertRuleDetail({ id, rule: r, loading, error, onRetry }: AlertRuleDetailProps) {
  const t = useTranslations("alertDetail");
  return (
    <EntityDetailShell
      loading={loading}
      notFound={!r && !error}
      breadcrumb={{ label: t("alerts"), href: "/alerts" }}
      heading={r ? r.name : t("ruleHeading", { id: id.slice(0, 8) })}
      status={r?.severity}
      statusTone={r ? (SEVERITY_TONE[r.severity] ?? "muted") : undefined}
      createdAt={r?.createdAt}
      notFoundLabel={t("ruleNoun")}
      overview={
        r
          ? [
              {
                term: t("state"),
                description: r.activeMute ? (
                  <Badge variant="secondary">{t("muted")}</Badge>
                ) : r.isActive ? (
                  <Badge variant="secondary">{t("active")}</Badge>
                ) : (
                  <Badge variant="outline">{t("inactive")}</Badge>
                ),
              },
              {
                term: t("target"),
                description: (
                  <span>
                    <Badge variant="outline">{r.target}</Badge>
                    {r.targetId ? (
                      <span className="text-muted-foreground ml-2 font-mono text-xs">
                        {r.targetId}
                      </span>
                    ) : null}
                  </span>
                ),
              },
              {
                term: t("severity"),
                description: <span className="capitalize">{r.severity}</span>,
              },
              {
                term: t("managedService"),
                description: r.managedServiceId ? (
                  <span className="font-mono text-xs">{r.managedServiceId}</span>
                ) : (
                  <span className="text-muted-foreground">—</span>
                ),
              },
              {
                term: t("organization"),
                description: <span className="font-mono text-xs">{r.organizationSlug}</span>,
              },
              { term: t("created"), description: <DetailTimestamp iso={r.createdAt} /> },
              { term: t("updated"), description: <DetailTimestamp iso={r.updatedAt} /> },
              ...(r.activeMute
                ? [
                    {
                      term: t("mutedUntil"),
                      description: <DetailTimestamp iso={r.activeMute.ttlUntil} />,
                    },
                    { term: t("muteReason"), description: r.activeMute.reason || "—" },
                    {
                      term: t("mutedBy"),
                      description: (
                        <span className="font-mono text-xs">{r.activeMute.createdBy}</span>
                      ),
                    },
                  ]
                : []),
            ]
          : []
      }
    >
      {error ? <QueryError title={t("loadError")} error={error} onRetry={onRetry} /> : null}
      {r ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("predicate")}</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
                {prettyJson(r.predicate)}
              </pre>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">{t("notifyChannels")}</CardTitle>
            </CardHeader>
            <CardContent>
              <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
                {prettyJson(r.notifyChannels)}
              </pre>
            </CardContent>
          </Card>
        </>
      ) : null}
    </EntityDetailShell>
  );
}
