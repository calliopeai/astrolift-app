"use client";

import { useQuery } from "@apollo/client/react";

import { GET_APP_IDENTITY_BINDING } from "@/graphql/lifecycle/lifecycle.queries";

import type { WorkloadIdentityCardData } from "./WorkloadIdentityCard";

/** The data half of WorkloadIdentityCard. */
export function useWorkloadIdentity(appSlug: string, environmentName?: string) {
  const { data, loading, refetch } = useQuery<WorkloadIdentityCardData>(GET_APP_IDENTITY_BINDING, {
    variables: { appSlug, environmentName: environmentName ?? null },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });
  return { data: data ?? null, loading, onRefresh: () => void refetch() };
}
