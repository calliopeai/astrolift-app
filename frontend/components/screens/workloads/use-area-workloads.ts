"use client";

import * as React from "react";

import { useWalk } from "@/components/screens/apps/list/use-apps-list";
import { LIST_WORKLOADS_PAGE } from "@/graphql/registry/registry.queries";
import type { AstroliftWorkload } from "@/graphql/registry/registry.types";

import { areaWorkloads } from "./workloads-list";

// Stable, so the walk's variables key never changes between renders.
const NO_VARIABLES = {};

/**
 * Every agent, workflow and function workload in the org: the workload walk
 * with no search (the same variables the Apps list walks with, so the two
 * read one cache entry), narrowed to the Agents area's kinds. The Workloads
 * and Functions lists both read it.
 */
export function useAreaWorkloads() {
  const walk = useWalk<AstroliftWorkload>(
    LIST_WORKLOADS_PAGE,
    "astroliftWorkloadsPage",
    "after",
    NO_VARIABLES
  );
  const workloads = React.useMemo(() => areaWorkloads(walk.items), [walk.items]);
  return {
    workloads,
    loading: walk.loading,
    stale: walk.stale,
    error: walk.error ? { message: walk.error.message } : null,
    onRetry: () => {
      void walk.refetch();
    },
  };
}
