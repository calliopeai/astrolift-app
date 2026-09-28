"use client";

import { useQuery, useSubscription } from "@apollo/client/react";
import * as React from "react";

import { ON_APP_LOG } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

interface AppResp {
  astroliftApp: AstroliftRegisteredApp | null;
}

interface LogResp {
  astroliftOnAppLog: AstroliftAppLogLine;
}

export const LOG_BUFFER_LIMIT = 500;
const DEFAULT_TAIL_LINES = 200;

/**
 * The data half of AppLogsScreen: the app, and a live log tail of the
 * selected pod and container, capped at LOG_BUFFER_LIMIT lines.
 */
export function useAppLogs({
  slug,
  selectedPod,
  selectedContainer,
}: {
  slug: string;
  selectedPod: string | null;
  selectedContainer: string | null;
}) {
  const app = useQuery<AppResp>(GET_APP, { variables: { slug } });
  const a = app.data?.astroliftApp ?? null;

  const [streaming, setStreaming] = React.useState(false);
  const [logBuffer, setLogBuffer] = React.useState<AstroliftAppLogLine[]>([]);

  // Pre-buffer on open: as soon as a pod resolves flip streaming on once
  // so the subscription replays the last DEFAULT_TAIL_LINES before following.
  // A one-shot guard stops the 5s pod poll from re-arming after the operator pauses.
  const [autoStreamed, setAutoStreamed] = React.useState(false);
  if (!autoStreamed && selectedPod) {
    setAutoStreamed(true);
    setStreaming(true);
  }

  // Reset the buffer whenever the operator switches pod or container.
  // See: https://react.dev/learn/you-might-not-need-an-effect#resetting-all-state-when-a-prop-changes
  const streamKey = `${selectedPod ?? ""}::${selectedContainer ?? ""}`;
  const [prevStreamKey, setPrevStreamKey] = React.useState(streamKey);
  if (prevStreamKey !== streamKey) {
    setPrevStreamKey(streamKey);
    if (logBuffer.length !== 0) setLogBuffer([]);
  }

  useSubscription<LogResp>(ON_APP_LOG, {
    variables: {
      appSlug: slug,
      podName: selectedPod ?? "",
      container: selectedContainer ?? null,
      follow: true,
      tailLines: DEFAULT_TAIL_LINES,
    },
    skip: !streaming || !selectedPod,
    onData: ({ data }) => {
      const line = data.data?.astroliftOnAppLog;
      if (!line) return;
      setLogBuffer((prev) => {
        const next = [...prev, line];
        return next.length > LOG_BUFFER_LIMIT ? next.slice(-LOG_BUFFER_LIMIT) : next;
      });
    },
  });

  return {
    app: a,
    /** First load of the app only. */
    loading: app.loading && !a,
    streaming,
    toggleStreaming: () => setStreaming((s) => !s),
    lines: logBuffer,
    clearLines: () => setLogBuffer([]),
  };
}
