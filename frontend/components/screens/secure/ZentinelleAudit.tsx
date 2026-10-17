"use client";

import { DownloadIcon, ScrollTextIcon } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";

import { Feed } from "@/components/feed/Feed";
import { Button } from "@/components/ui/button";

import { ZentinelleScopeFilter, type ZentinelleScopeFilterProps } from "./ZentinelleScopeFilter";
import type { ZentinelleAuditEvent } from "./zentinelle-model";

export type ZentinelleExportFormat = "ndjson" | "csv";

/** The feed's state, as `useCursorFeed` hands it over. */
export interface ZentinelleAuditFeed {
  items: ZentinelleAuditEvent[];
  loading: boolean;
  error: string | null;
  onRetry?: () => void;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore?: () => void;
}

export interface ZentinelleAuditProps {
  filter: ZentinelleScopeFilterProps;
  feed: ZentinelleAuditFeed;
  /** zentinelle.audit_export. */
  canExport: boolean;
  exporting: boolean;
  onExport: (format: ZentinelleExportFormat) => void;
}

/** Zentinelle's evidence and audit stream for the agents the viewer may see. */
export function ZentinelleAudit({
  filter,
  feed,
  canExport,
  exporting,
  onExport,
}: ZentinelleAuditProps) {
  const t = useTranslations("zentinelleScreen.audit");
  const format = useFormatter();

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <ZentinelleScopeFilter {...filter} />
        {canExport && (
          <div className="flex gap-2">
            {(["ndjson", "csv"] as const).map((f) => (
              <Button
                key={f}
                size="sm"
                variant="outline"
                disabled={exporting}
                onClick={() => onExport(f)}
              >
                <DownloadIcon className="mr-1 size-4" />
                {t(`export.${f}`)}
              </Button>
            ))}
          </div>
        )}
      </div>
      <Feed
        label={t("label")}
        {...feed}
        keyOf={(e) => e.id}
        maxHeight="max-h-[32rem]"
        errorTitle={t("error")}
        loadOlderLabel={t("loadMore")}
        loadingOlderLabel={t("loadingMore")}
        empty={{ icon: <ScrollTextIcon />, title: t("empty"), description: t("emptyBody") }}
        renderItem={(event) => (
          <div className="flex min-w-0 flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="font-mono text-sm">{event.action}</span>
            <span className="text-muted-foreground min-w-0 flex-1 text-sm [overflow-wrap:anywhere]">
              {event.resourceName || event.resourceId}
              {event.agentSlug && ` · ${event.agentSlug}`}
            </span>
            <span className="text-muted-foreground text-xs">
              {event.actor && `${event.actor} · `}
              {event.occurredAt
                ? format.dateTime(new Date(event.occurredAt), {
                    dateStyle: "medium",
                    timeStyle: "short",
                  })
                : ""}
            </span>
          </div>
        )}
      />
    </div>
  );
}
