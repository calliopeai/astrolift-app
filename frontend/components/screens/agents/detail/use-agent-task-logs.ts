"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";
import { useTranslations } from "next-intl";

import {
  downloadTextFile,
  formatLogFile,
  logFilename,
} from "@/components/screens/apps/deployments/app-log-lines";
import type { AstroliftAgentTaskLogPage } from "@/graphql/__generated__/schema";
import { AGENT_TASK_LOGS_PAGE } from "@/graphql/agents/agents.queries";
import type { LogLevel, LogLine } from "@/components/run/LogView";

interface AgentTaskLogsResp {
  agentTaskLogsPage: AstroliftAgentTaskLogPage;
}

const LOG_TAIL = 200;

/**
 * Poll the newest page; freeze the snapshot while reading earlier pages.
 * The data half of AgentTaskLogsView.
 */
export function useAgentTaskLogs(taskId: string) {
  const t = useTranslations("agentObservation.logs");
  const [loadingEarlier, setLoadingEarlier] = React.useState(false);
  const [pageError, setPageError] = React.useState<string | null>(null);
  const [frozenPage, setFrozenPage] = React.useState<AstroliftAgentTaskLogPage | null>(null);
  const generation = React.useRef(0);
  const { data, loading, error, refetch, fetchMore, stopPolling, startPolling } =
    useQuery<AgentTaskLogsResp>(AGENT_TASK_LOGS_PAGE, {
      variables: { id: taskId, cursor: null, limit: LOG_TAIL },
      pollInterval: 5000,
      fetchPolicy: "cache-and-network",
    });
  const page = frozenPage ?? data?.agentTaskLogsPage;
  const lines = React.useMemo<LogLine[]>(
    () =>
      (page?.items ?? []).map((line) => ({
        ts: line.timestamp ?? "",
        message: line.stream === "stderr" ? `[stderr] ${line.message}` : line.message,
        level: line.level ? (line.level as LogLevel) : undefined,
      })),
    [page]
  );
  const refresh = () => {
    generation.current += 1;
    setFrozenPage(null);
    setLoadingEarlier(false);
    setPageError(null);
    startPolling(5000);
    void refetch({ id: taskId, cursor: null, limit: LOG_TAIL }).catch((cause: unknown) => {
      setPageError(cause instanceof Error ? cause.message : t("refreshFailed"));
    });
  };
  const loadEarlier = async () => {
    if (!page?.nextCursor || loadingEarlier) return;
    stopPolling();
    setFrozenPage(page);
    const current = generation.current;
    setLoadingEarlier(true);
    setPageError(null);
    try {
      const result = await fetchMore({
        variables: { id: taskId, cursor: page.nextCursor, limit: LOG_TAIL },
      });
      if (current === generation.current) {
        const earlier = result.data?.agentTaskLogsPage;
        if (!earlier) throw new Error(t("earlierFailed"));
        setFrozenPage({ ...earlier, items: [...earlier.items, ...page.items] });
      }
    } catch (cause) {
      if (current === generation.current) {
        setPageError(cause instanceof Error ? cause.message : t("earlierFailed"));
      }
    } finally {
      if (current === generation.current) setLoadingEarlier(false);
    }
  };

  return {
    lines,
    loading: loading && !data,
    error: error && !data ? error.message : null,
    onRetry: refresh,
    onRefresh: refresh,
    hasMore: page?.hasMore ?? false,
    loadingEarlier,
    pageError,
    liveOnly: page?.liveOnly ?? true,
    windowLimited: page?.windowLimited ?? false,
    onLoadEarlier: () => void loadEarlier(),
    onDownload: () =>
      downloadTextFile(logFilename([{ value: taskId, fallback: "run" }]), formatLogFile(lines)),
  };
}
