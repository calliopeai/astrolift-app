"use client";

import {
  AlertCircleIcon,
  BarChart3Icon,
  BoltIcon,
  GaugeIcon,
  LayersIcon,
  ScrollIcon,
} from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import * as React from "react";

import { DataTable, useCursorTable, type Column, type CursorPage } from "@/components/data-table";
import { EmptyState } from "@/components/EmptyState";
import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

// Tabs folded in from /observe/functions (#892). Fleet is the query-backed
// workload list; the four signal tabs are gateway placeholders — the
// function runtime hasn't shipped, so there are no metrics to back them yet.
type FunctionTab = "fleet" | "throughput" | "errors" | "latency" | "logs";
const FUNCTION_TABS: readonly FunctionTab[] = ["fleet", "throughput", "errors", "latency", "logs"];

const TAB_LABELS: Record<FunctionTab, string> = {
  fleet: "Fleet",
  throughput: "Throughput",
  errors: "Errors",
  latency: "Latency",
  logs: "Logs",
};

const TAB_ICONS: Record<FunctionTab, React.ReactNode> = {
  fleet: <LayersIcon className="size-4" />,
  throughput: <BarChart3Icon className="size-4" />,
  errors: <AlertCircleIcon className="size-4" />,
  latency: <GaugeIcon className="size-4" />,
  logs: <ScrollIcon className="size-4" />,
};

interface WorkloadsPageResp {
  astroliftWorkloadsPage: CursorPage<AstroliftWorkload>;
}

function FleetTab() {
  /**
   * Server-paginated workload walk, narrowed to `kind: function` (#1233).
   *
   * `astroliftWorkloadsPage` takes `appSlug`, `search`, `limit` and
   * `after` — there is no `kind` argument and no sort argument, so the
   * columns declare no `sortKey` (the old name/app client sort is gone,
   * tracked for the server side in #1239) and the kind narrowing has to
   * stay on the client for now.
   *
   * It runs inside `extract`, over the page the server returned, so the
   * rows on screen are always functions and never a truncated prefix of
   * every workload. Two consequences are honest and deliberate:
   * `totalCount` is dropped (the server counts every workload in the
   * org, which is not the number of functions, and a wrong count is
   * worse than none), and a page whose 25 workloads happen to contain no
   * function renders the empty state with Next still enabled. Both go
   * away when the field takes a `kinds:` filter — the same shape
   * `astroliftDeploymentsPage` already has for `statuses:` (#1242).
   */
  const table = useCursorTable<AstroliftWorkload>({
    query: LIST_WORKLOADS_PAGE,
    extract: (d) => {
      const page = (d as WorkloadsPageResp | undefined)?.astroliftWorkloadsPage;
      if (!page) return page;
      return {
        items: page.items.filter((w) => w.kind === "function"),
        nextCursor: page.nextCursor,
        totalCount: null,
      };
    },
    searchVariable: "search",
    urlKey: "fn",
  });

  const columns: Column<AstroliftWorkload>[] = [
    {
      id: "name",
      header: "Workload",
      cell: (w) => <span className="font-medium">{w.name}</span>,
    },
    {
      id: "app",
      header: "App",
      cell: (w) => <Badge variant="outline">{w.registeredAppSlug}</Badge>,
    },
    {
      id: "minScale",
      header: "Min scale",
      cellClassName: "text-muted-foreground text-sm",
      cell: (w) => w.hpaMinReplicas ?? 0,
    },
    {
      id: "maxScale",
      header: "Max scale",
      cellClassName: "text-muted-foreground text-sm",
      cell: (w) => w.hpaMaxReplicas ?? "—",
    },
  ];

  return (
    <DataTable
      label="Function workloads"
      controller={table}
      columns={columns}
      getRowId={(w) => w.id}
      rowHref={(w) => `/apps/${w.registeredAppSlug}/workloads/${w.slug}`}
      searchPlaceholder="Filter workloads..."
      empty={{
        icon: <BoltIcon className="size-5" />,
        title: "No function workloads",
        description:
          "Apps with kind: function in their manifest will appear here. Functions scale to zero and trigger on HTTP, queues, or events.",
      }}
      emptyFiltered={{
        title: "No matching function workloads",
        description:
          "No function workload on this page matches that search. The server matches workload name, slug and kind plus the owning app's slug.",
      }}
    />
  );
}

export function FunctionsClient() {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const rawTab = searchParams.get("tab") as FunctionTab | null;
  const tab: FunctionTab = rawTab && FUNCTION_TABS.includes(rawTab) ? rawTab : "fleet";

  function setTab(next: FunctionTab) {
    const params = new URLSearchParams(searchParams.toString());
    if (next === "fleet") {
      params.delete("tab");
    } else {
      params.set("tab", next);
    }
    const qs = params.toString();
    router.replace(`${pathname}${qs ? `?${qs}` : ""}`, { scroll: false });
  }

  return (
    <PageShell
      title="Functions"
      description="Event-driven container invocations — scale to zero, trigger on HTTP, queues, or webhooks. The runtime layer hasn't shipped yet; these signal surfaces activate once it does."
    >
      {/* Tab strip — URL-synced via ?tab= param. "fleet" is the default
          and omitted from the URL to keep the canonical /functions link clean. */}
      <div
        role="tablist"
        aria-label="Function signal tabs"
        className="bg-muted/40 inline-flex flex-wrap rounded-md border p-1"
      >
        {FUNCTION_TABS.map((tabKey) => {
          const active = tab === tabKey;
          return (
            <button
              key={tabKey}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => setTab(tabKey)}
              className={
                "inline-flex items-center gap-1.5 rounded px-3 py-1 text-sm font-medium transition " +
                (active
                  ? "bg-background text-foreground shadow-sm"
                  : "text-muted-foreground hover:text-foreground")
              }
            >
              {TAB_ICONS[tabKey]}
              {TAB_LABELS[tabKey]}
            </button>
          );
        })}
      </div>

      {tab === "fleet" && <FleetTab />}
      {tab === "throughput" && (
        <EmptyState
          icon={<BarChart3Icon className="size-5" />}
          title="Function Throughput"
          description="Requests per second and invocation count across all function workloads — by trigger source and app."
        />
      )}
      {tab === "errors" && (
        <EmptyState
          icon={<AlertCircleIcon className="size-5" />}
          title="Function Errors"
          description="Failed invocations with HTTP status code, error type, and trigger context."
        />
      )}
      {tab === "latency" && (
        <EmptyState
          icon={<GaugeIcon className="size-5" />}
          title="Function Latency"
          description="Cold start frequency, warm invocation latency (p50/p95/p99), and end-to-end duration."
        />
      )}
      {tab === "logs" && (
        <EmptyState
          icon={<ScrollIcon className="size-5" />}
          title="Function Logs"
          description="Invocation logs from function containers including request context and response code."
        />
      )}
    </PageShell>
  );
}
