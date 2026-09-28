"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_EVENTS } from "@/graphql/operations/operations.queries";
import type { AstroliftEvent } from "@/graphql/operations/operations.types";

interface EventsResp {
  astroliftEvents: AstroliftEvent[];
}

/**
 * Recent platform events for one app. Server-side filtered by appSlug so we
 * only load events belonging to this app (fixes the empty-feed bug where
 * client-side filtering compared the app GUID against the integer PK
 * serialised as a string). The data half of ActivityTimelineView.
 */
export function useActivityTimeline(appSlug: string) {
  const { data, loading } = useQuery<EventsResp>(LIST_EVENTS, {
    variables: { limit: 100, appSlug },
    fetchPolicy: "cache-and-network",
  });

  return { events: data?.astroliftEvents ?? [], loading };
}
