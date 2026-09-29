"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { AGENT_GALLERY } from "@/graphql/agents/agents.queries";
import { useActiveOrg } from "@/graphql/identity/identity.hooks";

import { GALLERY_POLL_MS, type GalleryTask } from "./AgentTheatre";

interface AgentGalleryData {
  agentGallery: GalleryTask[];
}

/** The org's running watchable agents, re-polled every 5s. The data half of AgentTheatre. */
export function useAgentGallery() {
  // Reactive org id (#1022): the synchronous cookie read races the
  // post-render effect that sets it, leaving orgId "" on cold load.
  const { org } = useActiveOrg();
  const orgId = org?.id ?? "";
  const { data, loading, error, refetch } = useQuery<AgentGalleryData>(AGENT_GALLERY, {
    variables: { orgId },
    fetchPolicy: "cache-and-network",
    pollInterval: GALLERY_POLL_MS,
    skip: !orgId,
  });

  // Manual refresh feedback. refetch() resolves fast and the roster often
  // looks identical (same tasks, snapshot still pending), so without a
  // spinner the button reads as dead ("doesn't do anything"). Drive the
  // icon spin + disable off an explicit in-flight flag.
  const [refreshing, setRefreshing] = React.useState(false);
  const onRefresh = React.useCallback(async () => {
    setRefreshing(true);
    try {
      await refetch();
    } finally {
      setRefreshing(false);
    }
  }, [refetch]);

  return {
    hasOrg: Boolean(orgId),
    tasks: data?.agentGallery ?? null,
    loading,
    error: error?.message ?? null,
    refreshing,
    onRefresh,
    onRetry: () => void refetch(),
  };
}
