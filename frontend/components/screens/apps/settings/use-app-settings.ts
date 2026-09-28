"use client";

import { useQuery } from "@apollo/client/react";

import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

/**
 * The app behind the settings landing. The data half of AppSettingsScreen;
 * each section below it owns its own hook.
 */
export function useAppSettings(slug: string) {
  const app = useQuery<AppResp>(GET_APP, {
    variables: { slug },
    fetchPolicy: "cache-and-network",
  });
  return {
    app: app.data?.astroliftApp ?? null,
    /** First load only: a refetch keeps showing the app it has. */
    loading: app.loading && !app.data,
  };
}
