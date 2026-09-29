"use client";

import { CheckIcon, MinusIcon, PlugIcon } from "lucide-react";
import Link from "next/link";
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

const MATRICES: { value: Matrix; label: string }[] = [
  { value: "capabilities", label: "Capability matrix" },
  { value: "managed", label: "Managed services per provider" },
];

function driversList(managedKinds: string[]): ListDefinition {
  return {
    id: "documentation.drivers",
    fields: [
      {
        key: "driver",
        label: "Driver",
        options: CAPABILITIES.map((c) => ({ value: c.key, label: c.label })),
      },
      {
        key: "service",
        label: "Managed service",
        options: managedKinds.map((k) => ({ value: k, label: k })),
      },
      {
        key: "bound",
        label: "Clusters",
        options: [
          { value: "bound", label: "clusters bound" },
          { value: "none", label: "no clusters" },
        ],
      },
    ],
    searchPlaceholder: "Search providers…",
    defaultSort: [{ key: "order", dir: "asc" }],
    views: standardViews({ owner: "me" }, [], {
      mineNote: "Provider plugins belong to the install, not a person, so Mine is empty.",
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
  rows,
  clusterCountByProvider,
  managedKindColumns,
  usingFallback,
}: DriversScreenProps) {
  const [matrix, setMatrix] = React.useState<Matrix>("capabilities");
  const def = React.useMemo(() => driversList(managedKindColumns), [managedKindColumns]);
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
    header: "Provider",
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
                {boundCount} {boundCount === 1 ? "cluster bound" : "clusters bound"}
              </Badge>
            </Link>
          ) : (
            <span className="text-muted-foreground text-2xs">No clusters</span>
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
              header: cap.label,
              align: "center",
              cell: (row) =>
                row.capabilities[cap.key] ? (
                  <CheckIcon className="text-success-fg mx-auto size-4" aria-label="supported" />
                ) : (
                  <MinusIcon
                    className="text-muted-foreground mx-auto size-4"
                    aria-label="not supported"
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
                  <Badge className="text-2xs">yes</Badge>
                ) : (
                  <span className="text-muted-foreground text-xs">no</span>
                ),
            })
          ),
        ];

  return (
    <PageShell
      title="Driver reference"
      description="Provider plugins and the per-capability driver implementations they advertise. The matrix below is sourced from the registered ProviderPlugin manifests; when no plugins are live, a static known-provider list is shown."
    >
      {loading ? (
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
                <CardTitle className="text-base">Static reference</CardTitle>
                <CardDescription>
                  No provider plugins are registered on this install yet. The table below shows the
                  canonical capability surface for each supported provider. Once plugins load, this
                  surface switches to the live manifest.
                </CardDescription>
              </CardHeader>
            </Card>
          )}

          <Card>
            <CardHeader className="gap-3">
              <div
                className="bg-muted/40 inline-flex w-fit max-w-full flex-wrap rounded-md border p-1"
                role="tablist"
                aria-label="Matrix"
              >
                {MATRICES.map((m) => (
                  <button
                    key={m.value}
                    type="button"
                    role="tab"
                    aria-selected={matrix === m.value}
                    onClick={() => setMatrix(m.value)}
                    className={cn(
                      "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition",
                      matrix === m.value
                        ? "bg-background text-foreground shadow-sm"
                        : "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {m.value === "capabilities" && <PlugIcon className="size-4" />}
                    {m.label}
                  </button>
                ))}
              </div>
              {matrix === "capabilities" ? (
                <CardDescription>
                  Each row is a provider; columns are the driver kinds it bundles. A check means the
                  provider implements that driver.
                </CardDescription>
              ) : (
                <CardDescription>
                  Which managed-service kinds each provider can provision. Bindings declared in{" "}
                  <code>[[managed_services]]</code> on the app manifest must match one of the kinds
                  listed here.
                </CardDescription>
              )}
            </CardHeader>
            <CardContent>
              <ListPage<DriverRow>
                embedded
                list={list}
                label="Providers"
                columns={columns}
                rows={page.rows}
                getRowId={(r) => r.slug}
                totalCount={page.totalCount}
                empty={{ icon: <PlugIcon className="size-5" />, title: "No providers" }}
              />
              {matrix === "capabilities" && (
                <p className="text-muted-foreground mt-4 text-xs">
                  Matrix reflects manifest declarations · last probed varies by cluster ·{" "}
                  <Link href="/clusters" className="text-primary hover:underline">
                    See live capabilities →
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
