"use client";

import { useQuery } from "@apollo/client/react";

import { LIST_AVAILABLE_REPOS, LIST_SOURCE_CONNECTIONS } from "@/graphql/scm/scm.queries";
import type { AstroliftRemoteRepoList, AstroliftSourceConnection } from "@/graphql/scm/scm.types";

import type { RepoStepData } from "./RepoStep";

/** The repo step's source connections, and the repos of `connectionId` once one is chosen. */
export function useRepoStepData(connectionId: string): RepoStepData {
  const connections = useQuery<{ astroliftSourceConnections: AstroliftSourceConnection[] }>(
    LIST_SOURCE_CONNECTIONS,
    { fetchPolicy: "cache-and-network" }
  );
  const repos = useQuery<{ astroliftAvailableRepos: AstroliftRemoteRepoList }>(
    LIST_AVAILABLE_REPOS,
    {
      variables: { connectionId, limit: 100 },
      skip: !connectionId,
      fetchPolicy: "cache-and-network",
    }
  );
  return {
    connections: connections.data?.astroliftSourceConnections ?? [],
    connectionsLoading: connections.loading,
    repos: repos.data?.astroliftAvailableRepos ?? null,
    reposLoading: repos.loading,
  };
}
