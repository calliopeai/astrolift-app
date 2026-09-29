"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftSourceConnection } from "@/graphql/scm/scm.types";

interface ConnectionsResp {
  astroliftSourceConnections: AstroliftSourceConnection[];
}

/** The viewer's source connections. The data half of GithubConnectCalloutView. */
export function useSourceConnections() {
  const { data, loading } = useQuery<ConnectionsResp>(LIST_SOURCE_CONNECTIONS, {
    fetchPolicy: "cache-and-network",
  });
  return { loading, connections: data?.astroliftSourceConnections ?? [] };
}
