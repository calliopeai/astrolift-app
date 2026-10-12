"use client";

import { QueryError } from "@/components/QueryError";

import { CheckIcon, MinusIcon, PlugIcon } from "lucide-react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import * as React from "react";

import type { Column } from "@/components/data-table";
import { ListPage } from "@/components/list/ListPage";
import { type SelectRowsSpec, selectRows } from "@/components/list/select-rows";
import {
  type ListDefinition,
  standardViews,
  useLocalListState,
} from "@/components/list/use-list-state";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

import { CAPABILITIES, type DriverRow, type useDrivers } from "./use-drivers";

export type DriversScreenProps = ReturnType<typeof useDrivers>;

/** The one list of providers, read as either of its two matrices. */
type Matrix = "capabilities" | "managed";

const MATRICES: Matrix[] = ["capabilities", "managed"];

type Translate = (key: string) => string;

function driversList(managedKinds: string[], t: Translate): ListDefinition {
  return {
    id: "documentation.drivers",
    fields: [
      {
        key: "driver",
        label: t("filters.driver"),
        options: CAPABILITIES.map((c) => ({ value: c.key, label: t(`capabilities.${c.key}`) })),
      },
      {
        key: "service",
        label: t("filters.service"),
        options: managedKinds.map((k) => ({ value: k, label: k })),
      },
      {
        key: "bound",
        label: t("filters.clusters"),
        options: [
          { value: "bound", label: t("filters.bound") },
          { value: "none", label: t("filters.none") },
        ],
      },
    ],
    searchPlaceholder: t("searchPlaceholder"),
    defaultSort: [{ key: "order", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], {
      mineNote: t("mineNote"),
    }),
    paging: "numbered",
    pageSizes: [25, 50, 100],
  };
}

function driversSelect(
  rows: DriverRow[],
  clusterCountByProvider: Map<string, number>
): SelectRowsSpec<DriverRow> {
  const order = new Map(rows.map((r, i) => [r.slug, i]));
  const bound = (r: DriverRow) => clusterCountByProvider.get(r.slug) ?? 0;
  return {
    filter: {
      owner: () => false,
      driver: (r, value) => Boolean(r.capabilities[value]),
      service: (r, value) => r.managedServices.includes(value),
      bound: (r, value) => (value === "bound" ? bound(r) > 0 : bound(r) === 0),
    },
    text: (r) => [r.name, r.slug, r.version],
    sort: {
      order: (r) => order.get(r.slug) ?? 0,
      name: (r) => r.name.toLowerCase(),
      bound,
    },
    id: (r) => r.slug,
  };
}

export function DriversScreen({
  loading,
  error,
  onRetry,
  rows,
  clusterCountByProvider,
  managedKindColumns,
  usingFallback,
}: DriversScreenProps) {
  const t = useTranslations("docsDrivers");
  const [matrix, setMatrix] = React.useState<Matrix>("capabilities");
  const def = React.useMemo(() => driversList(managedKindColumns, t), [managedKindColumns, t]);
  const list = useLocalListState(def);
  const page = selectRows(
    rows,
    {
      filters: list.filters,
      q: list.state.q,
      sort: list.state.sort,
      page: list.state.page,
      pageSize: list.state.pageSize,
    },
    driversSelect(rows, clusterCountByProvider)
  );

  const provider: Column<DriverRow> = {
    id: "provider",
    header: t("providerColumn"),
    sortKey: "name",
    width: matrix === "capabilities" ? "w-[260px]" : "w-[200px]",
    cell: (row) => {
      if (matrix === "managed") return <span className="text-sm font-medium">{row.name}</span>;
      const boundCount = clusterCountByProvider.get(row.slug) ?? 0;
      return (
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">{row.name}</span>
          <span className="text-muted-foreground font-mono text-xs">
            {row.slug}
            {row.version ? ` · v${row.version}` : ""}
          </span>
          {boundCount > 0 ? (
            <Link href="/clusters" className="inline-flex w-fit">
              <Badge variant="secondary" className="text-2xs">
                {t("clustersBound", { count: boundCount })}
              </Badge>
            </Link>
          ) : (
            <span className="text-muted-foreground text-2xs">{t("noClusters")}</span>
          )}
        </div>
      );
    },
  };

  const columns: Column<DriverRow>[] =
    matrix === "capabilities"
      ? [
          provider,
          ...CAPABILITIES.map(
            (cap): Column<DriverRow> => ({
              id: cap.key,
              header: t(`capabilities.${cap.key}`),
              align: "center",
              cell: (row) =>
                row.capabilities[cap.key] ? (
                  <CheckIcon
                    className="text-success-fg mx-auto size-4"
                    aria-label={t("supported")}
                  />
                ) : (
                  <MinusIcon
                    className="text-muted-foreground mx-auto size-4"
                    aria-label={t("notSupported")}
                  />
                ),
            })
          ),
        ]
      : [
          provider,
          ...managedKindColumns.map(
            (kind): Column<DriverRow> => ({
              id: `managed-${kind}`,
              header: kind,
              align: "center",
              cell: (row) =>
                row.managedServices.includes(kind) ? (
                  <Badge className="text-2xs">{t("yes")}</Badge>
                ) : (
                  <span className="text-muted-foreground text-xs">{t("no")}</span>
                ),
            })
          ),
        ];

  return (
    <PageShell title={t("title")} description={t("description")}>
      {error ? (
        <QueryError title={t("loadError")} error={error} onRetry={onRetry} />
      ) : loading ? (
        <Card>
          <CardContent className="p-6">
            <Skeleton className="h-44 w-full" />
          </CardContent>
        </Card>
      ) : (
        <>
          {usingFallback && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">{t("staticTitle")}</CardTitle>
                <CardDescription>{t("staticBody")}</CardDescription>
              </CardHeader>
            </Card>
          )}

          <Card>
            <CardHeader className="gap-3">
              <div
                className="bg-muted/40 inline-flex w-fit max-w-full flex-wrap rounded-md border p-1"
                role="tablist"
                aria-label={t("matrixLabel")}
              >
                {MATRICES.map((m) => (
                  <button
                    key={m}
                    type="button"
                    role="tab"
                    aria-selected={matrix === m}
                    onClick={() => setMatrix(m)}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition",
                      matrix === m
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {m === "capabilities" && <PlugIcon className="size-4" />}
                    {t(`matrices.${m}`)}
                  </button>
                ))}
              </div>
              {matrix === "capabilities" ? (
                <CardDescription>{t("capabilitiesBody")}</CardDescription>
              ) : (
                <CardDescription>
                  {t.rich("managedBody", { code: (chunks) => <code>{chunks}</code> })}
                </CardDescription>
              )}
            </CardHeader>
            <CardContent>
              <ListPage<DriverRow>
                embedded
                list={list}
                label={t("providersLabel")}
                columns={columns}
                rows={page.rows}
                getRowId={(r) => r.slug}
                totalCount={page.totalCount}
                empty={{ icon: <PlugIcon className="size-5" />, title: t("emptyTitle") }}
              />
              {matrix === "capabilities" && (
                <p className="text-muted-foreground mt-4 text-xs">
                  {t("matrixFootnote")}{" "}
                  <Link href="/clusters" className="text-primary hover:underline">
                    {t("seeLive")}
                  </Link>
                </p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </PageShell>
  );
}
