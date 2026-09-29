"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_APP_DNS_RECORDS } from "@/graphql/lifecycle/lifecycle.queries";

import type { DnsRecordsCardData } from "./DnsRecordsCard";

/** The data half of DnsRecordsCard. */
export function useDnsRecords(appSlug: string, environmentName?: string) {
  const { data, loading, refetch } = useQuery<DnsRecordsCardData>(LIST_APP_DNS_RECORDS, {
    variables: { appSlug, environmentName: environmentName ?? null },
    fetchPolicy: "cache-and-network",
    notifyOnNetworkStatusChange: true,
  });
  return { data: data ?? null, loading, onRefresh: () => void refetch() };
}
