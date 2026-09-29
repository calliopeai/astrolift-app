"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_APP_CERTIFICATES } from "@/graphql/lifecycle/lifecycle.queries";

import type { TlsCertificatesCardData } from "./TlsCertificatesCard";

/** The data half of TlsCertificatesCard. */
export function useTlsCertificates(appSlug: string, environmentName?: string) {
  const { data, loading, refetch } = useQuery<TlsCertificatesCardData>(LIST_APP_CERTIFICATES, {
    variables: { appSlug, environmentName: environmentName ?? null },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });
  return { data: data ?? null, loading, onRefresh: () => void refetch() };
}
