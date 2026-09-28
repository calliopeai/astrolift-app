"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";

import { useCursorTable, type CursorPage } from "@/components/data-table";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

// Tabs folded in from /observe/functions (#892). Fleet is the query-backed
// workload list; the four signal tabs are gateway placeholders — the
// function runtime hasn't shipped, so there are no metrics to back them yet.
export type FunctionTab = "fleet" | "throughput" | "errors" | "latency" | "logs";
export const FUNCTION_TABS: readonly FunctionTab[] = [
  "fleet",
  "throughput",
  "errors",
  "latency",
  "logs",
];

interface WorkloadsPageResp {
  astroliftWorkloadsPage: CursorPage<AstroliftWorkload>;
}

/**
 * The active Functions tab, URL-synced via `?tab=`. "fleet" is the default
 * and omitted from the URL to keep the canonical /functions link clean.
 */
export function useFunctionsTab() {
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

  return { tab, setTab };
}

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
export function useFunctionWorkloads() {
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
  return { table };
}
