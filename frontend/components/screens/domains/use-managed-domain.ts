"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { LIST_MANAGED_DOMAINS } from "@/graphql/clusters/clusters.queries";
import type { AstroliftManagedDomain } from "@/graphql/clusters/clusters.types";

interface Resp {
  astroliftManagedDomains: AstroliftManagedDomain[];
}

/**
 * One managed domain by id. Reuses LIST_MANAGED_DOMAINS (no singular query
 * exists). The data half of ManagedDomainDetail.
 */
export function useManagedDomain(id: string) {
  const { data, loading } = useQuery<Resp>(LIST_MANAGED_DOMAINS, {
    fetchPolicy: "cache-and-network",
  });

  const domain = React.useMemo(
    () => (data?.astroliftManagedDomains ?? []).find((row) => row.id === id) ?? null,
    [data, id]
  );

  return { loading, domain };
}

export type ManagedDomainState = ReturnType<typeof useManagedDomain>;
