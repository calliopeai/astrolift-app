"use client";

import { useTranslations } from "next-intl";
import Link from "next/link";

import {
  DetailTimestamp,
  EntityDetailShell,
  type Dot,
} from "@/components/detail/EntityDetailShell";
import { QueryError } from "@/components/QueryError";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { prettyJson } from "./alert-format";
import type { useAlertEventDetail } from "./use-alert-event-detail";

export type AlertEventDetailProps = Omit<
  ReturnType<typeof useAlertEventDetail>,
  "error" | "onRetry"
> & { error?: string | null; onRetry?: () => void };

/** Alert event detail (#1106) — the payload/timeline behind a fired alert. */
export function AlertEventDetail({ id, event: e, loading, error, onRetry }: AlertEventDetailProps) {
  const t = useTranslations("alertDetail");
  const state = e
    ? e.resolvedAt
      ? "resolved"
      : e.acknowledgedAt
        ? "acknowledged"
        : "firing"
    : undefined;
  const stateTone: Dot | undefined =
    state === "firing"
      ? "error"
      : state === "acknowledged"
        ? "warn"
        : state === "resolved"
          ? "muted"
          : undefined;

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!e && !error}
      breadcrumb={{ label: t("alerts"), href: "/alerts" }}
      heading={t("eventHeading", { id: (e ? e.id : id).slice(0, 8) })}
      status={state}
      statusTone={stateTone}
      notFoundLabel={t("eventNoun")}
      overview={
        e
          ? [
              { term: t("summary"), description: e.summary || "—" },
              {
                term: t("severity"),
                description: <span className="capitalize">{e.severity}</span>,
              },
              {
                term: t("rule"),
                description: (
                  <Link
                    href={`/alerts/rules/${e.ruleId}`}
                    className="font-mono text-xs text-[var(--brand-primary)] hover:underline"
                  >
                    {e.ruleId}
                  </Link>
                ),
              },
              { term: t("fired"), description: <DetailTimestamp iso={e.firedAt} /> },
              { term: t("acknowledged"), description: <DetailTimestamp iso={e.acknowledgedAt} /> },
              { term: t("resolved"), description: <DetailTimestamp iso={e.resolvedAt} /> },
            ]
          : []
      }
    >
      {error ? <QueryError title={t("loadError")} error={error} onRetry={onRetry} /> : null}
      {e ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("detail")}</CardTitle>
          </CardHeader>
          <CardContent>
            {Object.keys(e.detail ?? {}).length > 0 ? (
              <pre className="bg-muted/40 max-h-96 overflow-auto rounded-md border p-3 font-mono text-xs">
                {prettyJson(e.detail)}
              </pre>
            ) : (
              <p className="text-muted-foreground text-sm">{t("noDetail")}</p>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
