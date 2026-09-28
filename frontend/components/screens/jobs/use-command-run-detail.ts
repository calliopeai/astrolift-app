"use client";

import { useApolloClient, useQuery } from "@apollo/client/react";
import * as React from "react";

import { downloadText, logText, useNow } from "@/components/screens/deployments/run-support";
import { GET_COMMAND_RUN, LIST_COMMAND_RUNS } from "@/graphql/lifecycle/lifecycle.queries";
import type { AstroliftCommandRun } from "@/graphql/lifecycle/lifecycle.types";

import { outputLines } from "./jobs-list";

interface CommandRunResp {
  astroliftCommandRun: AstroliftCommandRun | null;
}

interface CommandRunListResp {
  astroliftCommandRuns: AstroliftCommandRun[];
}

/**
 * Command (one-off exec) run by id (#1106, #1118). Prefers the singular
 * `astroliftCommandRun(id)` query so a cold deep-link to a run outside the
 * 100-row window still resolves, falling back to the row in the cached
 * global LIST_COMMAND_RUNS window. Polls while the command runs, ticks its
 * clock and builds the log download. The data half of CommandRunDetail.
 */
export function useCommandRunDetail(id: string) {
  const client = useApolloClient();
  const { data, loading, error, refetch, startPolling, stopPolling } = useQuery<CommandRunResp>(
    GET_COMMAND_RUN,
    {
      variables: { id },
      fetchPolicy: "cache-and-network",
    }
  );

  const cachedFromList = React.useMemo(() => {
    const listed = client.readQuery<CommandRunListResp>({
      query: LIST_COMMAND_RUNS,
      variables: { limit: 100 },
    });
    return listed?.astroliftCommandRuns.find((r) => r.id === id) ?? null;
  }, [client, id]);

  const run = data?.astroliftCommandRun ?? cachedFromList;
  const running = Boolean(run && !run.endedAt);
  React.useEffect(() => {
    if (running) startPolling(5000);
    else stopPolling();
  }, [running, startPolling, stopPolling]);
  const now = useNow(running);

  function onDownload() {
    if (!run) return;
    downloadText(
      `command-${run.id.slice(0, 8)}.log`,
      logText(outputLines(run.output ?? "", run.startedAt ?? run.createdAt))
    );
  }

  return {
    loading,
    run,
    error: error && !run ? { message: error.message } : null,
    onRetry: () => {
      void refetch();
    },
    now,
    onDownload,
  };
}
