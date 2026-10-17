"use client";

import { useQuery } from "@apollo/client/react";

import type {
  GetBrainStatusQuery,
  GetBrainStatusQueryVariables,
} from "@/graphql/__generated__/operations";
import { GET_BRAIN_STATUS } from "@/graphql/registry/brains.queries";

import type { BrainStatusResult } from "./BrainStatusCell";

/**
 * One layer's status in one environment (#2424 `astroliftBrainStatus`). It
 * reads the source host (branch head, the compile, the last ingest), so the
 * list asks once per visible row and keeps the answer (`cache-first`); a
 * detail tab asks again on arrival. The overview, the substrate tab and the
 * list rows share the one cache entry per slug and environment.
 */
export function useBrainStatus(
  slug: string,
  environmentName: string,
  { skip = false, fresh = false }: { skip?: boolean; fresh?: boolean } = {}
) {
  const query = useQuery<GetBrainStatusQuery, GetBrainStatusQueryVariables>(GET_BRAIN_STATUS, {
    variables: { slug, environmentName },
    skip: skip || !slug,
    fetchPolicy: fresh ? "cache-and-network" : "cache-first",
  });
  const data = query.data ?? query.previousData;
  const result: BrainStatusResult =
    query.error && !data
      ? { state: "error", message: query.error.message }
      : data
        ? { state: "ready", status: data.astroliftBrainStatus ?? null }
        : { state: "loading" };
  return {
    result,
    /** A refetch in flight with an answer on screen. */
    refreshing: query.loading && Boolean(data),
    onRetry: () => {
      void query.refetch().catch(() => {});
    },
  };
}
