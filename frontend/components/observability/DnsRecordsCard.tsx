"use client";

/**
 * DnsRecordsCard — operator-facing DNS read panel for the app detail
 * observability subroute (#377). Shows live A / CNAME / TXT records
 * sourced from the cluster's DnsDriver, with a propagation status
 * dot per row.
 *
 * Empty state is keyed off the resolver's `reason` (#1111) so the card
 * renders one honest message — "Not configured", "Not available on this
 * cloud", "No DNS records yet", or "Couldn't load" — instead of the old
 * hedged "either/or" copy. The records are the card's embedded list
 * (search, type and propagation filters, sort, numbered pages over the
 * driver's answer), with its list state kept in the card.
 */

import { GlobeIcon, RefreshCwIcon, RotateCcwIcon } from "lucide-react";
import * as React from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

import { Can } from "@/components/Can";
import type { Column } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { ListPage } from "@/components/list/ListPage";
import { selectRows } from "@/components/list/select-rows";
import { useLocalListState } from "@/components/list/use-list-state";
import {
  type ObservabilityPanelReason,
  panelEmptyState,
} from "@/components/observability/panel-reason";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import type {
  AstroliftAppDnsRecord,
  DnsPropagationStatus,
} from "@/graphql/lifecycle/lifecycle.types";

import { localizeList } from "./localize-list";
import { DNS_RECORDS_LIST, DNS_RECORDS_SELECT, dnsKey } from "./network-lists";

export interface DnsRecordsCardData {
  astroliftAppDnsRecords: {
    reason: ObservabilityPanelReason;
    records: AstroliftAppDnsRecord[];
  };
}

const PROPAGATION_DOT: Record<DnsPropagationStatus, "ok" | "warn" | "muted"> = {
  propagated: "ok",
  pending: "warn",
  unknown: "muted",
};

export interface DnsRecordsCardProps {
  appSlug: string;
  /** Optional — names a specific environment's cluster to query. */
}

/** Pure (Storybook first): the data comes from useDnsRecords. */
export function DnsRecordsCard({
  appSlug,
  data,
  loading,
  onRefresh,
}: DnsRecordsCardProps & {
  data: DnsRecordsCardData | null;
  loading: boolean;
  onRefresh: () => void;
}) {
  const t = useTranslations("obsDnsRecords");
  const reasonT = useTranslations("obsPanelReason");
  const listT = useTranslations("obsLists");
  const records = data?.astroliftAppDnsRecords?.records ?? [];
  const reason = data?.astroliftAppDnsRecords?.reason;
  const isEmptyAfterLoad = !loading && records.length === 0;
  const empty = panelEmptyState(
    reason ?? "NO_DATA_YET",
    {
      noData: { title: t("noDataTitle"), description: t("noDataDescription") },
      notConfigured: t("notConfigured"),
    },
    reasonT
  );
  // A deep-link to fix it only makes sense when the operator can act
  // (not-configured / no-data). NOT_SUPPORTED is informational; ERROR
  // is transient.
  const showSetupAction = reason === "NOT_CONFIGURED" || reason === "NO_DATA_YET" || reason == null;

  const dnsList = React.useMemo(() => localizeList(DNS_RECORDS_LIST, "dnsRecords", listT), [listT]);
  const list = useLocalListState(dnsList);
  const page = selectRows(
    records,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    DNS_RECORDS_SELECT
  );
  const columns: Column<AstroliftAppDnsRecord>[] = [
    {
      id: "name",
      header: t("columns.name"),
      sortKey: "name",
      cellClassName: "max-w-72",
      cell: (r) => (
        <span className="block truncate font-mono text-xs" title={r.name}>
          {r.name}
        </span>
      ),
    },
    {
      id: "type",
      header: t("columns.type"),
      sortKey: "type",
      cellClassName: "font-mono text-xs",
      cell: (r) => r.type,
    },
    {
      id: "value",
      header: t("columns.value"),
      cellClassName: "text-muted-foreground max-w-80",
      cell: (r) => (
        <span className="block truncate font-mono text-xs" title={r.value}>
          {r.value}
        </span>
      ),
    },
    {
      id: "ttl",
      header: t("columns.ttl"),
      sortKey: "ttl",
      align: "right",
      cellClassName: "font-mono text-xs",
      cell: (r) => r.ttl,
    },
    {
      id: "propagation",
      header: t("columns.propagation"),
      cell: (r) => (
        <span className="inline-flex items-center gap-2 text-xs">
          <StatusDot status={PROPAGATION_DOT[r.propagationStatus]} />
          <span>{t(`propagation.${r.propagationStatus}`)}</span>
        </span>
      ),
    },
  ];

  return (
    <Card>
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0 pb-3">
        <div>
          <CardTitle className="flex items-center gap-2 text-base">
            <GlobeIcon className="size-4" /> {t("title")}
          </CardTitle>
          <CardDescription>
            {t.rich("description", { code: (chunks) => <code>{chunks}</code> })}
          </CardDescription>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              onRefresh();
            }}
            disabled={loading}
          >
            <RefreshCwIcon className="size-3" /> {t("refresh")}
          </Button>
          <Can permission="app.update">
            <Button
              size="sm"
              variant="outline"
              onClick={() => {
                // Force re-bind — the platform re-applies the manifest
                // annotation that triggers external-dns / route53
                // controller. The mutation surface lives on the
                // domain-handshake side (#397); until that's wired into
                // a generic "rebind DNS for app", this surfaces a toast
                // pointing operators at the per-domain re-validate path.
                toast.message(t("forceRebind"), {
                  description: t("forceRebindDescription"),
                });
              }}
            >
              <RotateCcwIcon className="size-3" /> {t("forceRebind")}
            </Button>
          </Can>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {loading && records.length === 0 ? (
          <div className="space-y-2 p-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : isEmptyAfterLoad ? (
          <div className="p-6">
            <EmptyState
              icon={<GlobeIcon className="size-5" />}
              title={empty.title}
              description={empty.description}
              actionHref={showSetupAction ? `/apps/${appSlug}/domains` : undefined}
              actionLabel={showSetupAction ? t("setUpDns") : undefined}
              secondary={
                reason === "ERROR" ? (
                  <Button size="sm" variant="outline" onClick={() => onRefresh()}>
                    {t("tryAgain")}
                  </Button>
                ) : undefined
              }
            />
          </div>
        ) : (
          <div className="px-4 pb-4">
            <ListPage<AstroliftAppDnsRecord>
              embedded
              list={list}
              label={t("title")}
              columns={columns}
              rows={page.rows}
              getRowId={dnsKey}
              totalCount={page.totalCount}
              empty={{ icon: <GlobeIcon className="size-5" />, title: empty.title }}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}
