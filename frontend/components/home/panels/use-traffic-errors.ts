"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import type { ObservabilityPanelReason } from "@/components/observability/panel-reason";
import type { AstroliftAppGoldenSignal } from "@/graphql/__generated__/schema";
import { GET_APP_GOLDEN_SIGNALS } from "@/graphql/observability/observability.queries";

import { combineReads, readState, useHomeMyApps } from "./home-reads";
import type { SignalSeries, TrafficErrorsPanelViewProps } from "./TrafficErrorsPanel";

/** The window the panel draws. */
export const TRAFFIC_RANGE_SECONDS = 24 * 60 * 60;

interface GoldenSignalsResp {
  astroliftAppGoldenSignals: {
    reason: ObservabilityPanelReason;
    signals: AstroliftAppGoldenSignal[];
  };
}

/** One signal's samples, oldest first, or null when the cluster sent none. */
export function signalSeries(
  signals: AstroliftAppGoldenSignal[],
  name: AstroliftAppGoldenSignal["name"]
): SignalSeries | null {
  const s = signals.find((x) => x.name === name);
  if (!s) return null;
  return {
    values: [...s.samples]
      .sort((a, b) => Date.parse(String(a.ts)) - Date.parse(String(b.ts)))
      .map((p) => p.value),
    unit: s.unit,
  };
}

/**
 * Traffic & errors' data: the five apps My apps reads (the same variables,
 * one cache entry) and the golden signals of the one picked, the first by
 * default. Only the picked app's signals are fetched.
 */
export function useTrafficErrors(): Omit<TrafficErrorsPanelViewProps, "panel"> {
  const myApps = useHomeMyApps("top");
  const [picked, setPicked] = React.useState<string | null>(null);
  const apps = myApps.apps.map((a) => ({ slug: a.slug, name: a.name || a.slug }));
  const appSlug = (picked && apps.some((a) => a.slug === picked) ? picked : apps[0]?.slug) ?? null;

  const q = useQuery<GoldenSignalsResp>(GET_APP_GOLDEN_SIGNALS, {
    variables: {
      appSlug: appSlug ?? "",
      environmentName: null,
      workloadSlug: null,
      rangeSeconds: TRAFFIC_RANGE_SECONDS,
    },
    fetchPolicy: "cache-and-network",
    skip: !appSlug,
  });
  const result = (q.data ?? q.previousData)?.astroliftAppGoldenSignals;
  const signals = result?.signals ?? [];
  const sources = [myApps, ...(appSlug ? [readState(q, Boolean(result))] : [])];
  return {
    apps,
    appSlug,
    onAppChange: setPicked,
    traffic: signalSeries(signals, "TRAFFIC"),
    errors: signalSeries(signals, "ERRORS"),
    reason: result?.reason ?? null,
    ...combineReads(sources, Boolean(result)),
  };
}
