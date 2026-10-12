"use client";

import { ExternalLinkIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";

import { DetailTimestamp, EntityDetailShell } from "@/components/detail/EntityDetailShell";
import { QueryError } from "@/components/QueryError";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { DefinitionListItem } from "@/components/ui/definition-list";

import { eventPayloadText, resolveSourceHref } from "./event-source";
import type { useEventDetail } from "./use-events";

export type EventDetailProps = Omit<ReturnType<typeof useEventDetail>, "error" | "onRetry"> & {
  error?: string | null;
  onRetry?: () => void;
};

/** Event detail (#1106) — full payload + metadata for a single platform event. */
export function EventDetail({ id, event: e, loading, error, onRetry }: EventDetailProps) {
  const t = useTranslations("eventDetail");
  const sourceHref = e
    ? resolveSourceHref(e.resourceKind ?? "", e.resourceId ?? "", e.payload ?? {})
    : null;
  const sourceLabel = e ? `${e.resourceKind || "?"}:${e.resourceId || "?"}` : "";

  const overview: DefinitionListItem[] = e
    ? [
        {
          term: t("eventType"),
          description: <span className="font-mono text-xs">{e.eventType}</span>,
        },
        { term: t("occurred"), description: <DetailTimestamp iso={e.occurredAt} /> },
        {
          term: t("source"),
          description:
            !e.resourceKind && !e.resourceId ? (
              <span className="text-muted-foreground">—</span>
            ) : sourceHref ? (
              <Link
                href={sourceHref}
                className="inline-flex items-center gap-1 font-mono text-xs text-[var(--brand-primary)] hover:underline"
              >
                {sourceLabel} <ExternalLinkIcon className="size-3" />
              </Link>
            ) : (
              <span className="font-mono text-xs">{sourceLabel}</span>
            ),
        },
      ]
    : [];

  if (e?.registeredAppId) {
    overview.push({
      term: t("app"),
      description: <span className="font-mono text-xs break-all">{e.registeredAppId}</span>,
    });
  }
  if (e?.organizationId) {
    overview.push({
      term: t("organization"),
      description: <span className="font-mono text-xs break-all">{e.organizationId}</span>,
    });
  }
  if (e?.teamId) {
    overview.push({
      term: t("team"),
      description: <span className="font-mono text-xs break-all">{e.teamId}</span>,
    });
  }
  if (e?.projectId) {
    overview.push({
      term: t("project"),
      description: <span className="font-mono text-xs break-all">{e.projectId}</span>,
    });
  }

  return (
    <EntityDetailShell
      loading={loading}
      notFound={!e && !error}
      breadcrumb={{ label: t("events"), href: "/events" }}
      heading={e ? e.eventType : t("eventHeading", { id: id.slice(0, 8) })}
      notFoundLabel={t("eventNoun")}
      overview={overview}
    >
      {error ? <QueryError title={t("loadError")} error={error} onRetry={onRetry} /> : null}
      {e ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">{t("payload")}</CardTitle>
          </CardHeader>
          <CardContent>
            {eventPayloadText(e.payload) !== null ? (
              <pre className="bg-muted/40 max-h-[32rem] overflow-auto rounded-md border p-3 font-mono text-xs">
                {JSON.stringify(e.payload, null, 2)}
              </pre>
            ) : (
              <p className="text-muted-foreground text-sm">{t("noPayload")}</p>
            )}
          </CardContent>
        </Card>
      ) : null}
    </EntityDetailShell>
  );
}
