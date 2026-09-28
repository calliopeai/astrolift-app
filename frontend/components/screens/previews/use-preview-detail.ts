"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_PREVIEW_ENVIRONMENTS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftPreviewEnvironment } from "@/graphql/lifecycle/lifecycle.types";

interface Resp {
  astroliftPreviewEnvironments: AstroliftPreviewEnvironment[];
}

/**
 * The data half of PreviewDetailScreen (#1106). Reuses the global
 * LIST_PREVIEW_ENVIRONMENTS query (no singular query exists), a cache hit
 * when navigated from /previews.
 */
export function usePreviewDetail(id: string) {
  const { data, loading } = useQuery<Resp>(LIST_PREVIEW_ENVIRONMENTS, {
    variables: { appSlug: null },
    fetchPolicy: "cache-and-network",
  });

  const preview = React.useMemo(
    () => (data?.astroliftPreviewEnvironments ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return { id, preview, loading };
}
