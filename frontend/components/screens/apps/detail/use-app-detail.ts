"use client";

import { useQuery } from "@apollo/client/react";

import { GET_APP, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp, AstroliftWorkload } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}
interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}

/**
 * The app behind the overview: the app record (with its drift rollup) and
 * its workloads. The data half of AppDetailScreen. The header's Deploy and
 * Delete moved to the app frame (use-app-frame).
 */
export function useAppDetail(slug: string) {
  // includeDrift opts the resolver into the config-drift rollup
  // (#407 C). The overview is the only caller that needs it; sibling
  // queries that hit GET_APP without the flag keep the cheap shape.
  const app = useQuery<AppResp>(GET_APP, {
    variables: { slug, includeDrift: true },
  });
  const workloads = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });

  return {
    loading: app.loading && !app.data,
    app: app.data?.astroliftApp ?? null,
    workloads: workloads.data?.astroliftWorkloads ?? [],
  };
}
