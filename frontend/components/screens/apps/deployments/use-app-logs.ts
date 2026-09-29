"use client";

import { useQuery, useSubscription } from "@apollo/client/react";
import { useTranslations } from "next-intl";
import * as React from "react";
import { toast } from "sonner";

import { ON_APP_LOG } from "@/graphql/lifecycle/lifecycle.subscriptions";
import type { AstroliftAppLogLine } from "@/graphql/lifecycle/lifecycle.types";
import { GET_APP } from "@/graphql/registry/registry.queries";
import type { AstroliftRegisteredApp } from "@/graphql/registry/registry.types";

import { downloadTextFile, formatLogFile, logFilename, toLogLines } from "./app-log-lines";

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
 * selected pod and container, capped at LOG_BUFFER_LIMIT lines, and the
 * download of that buffer.
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
  const t = useTranslations("apps.logViewer");
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

  const sub = useSubscription<LogResp>(ON_APP_LOG, {
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
    /** The subscription failed; the buffer so far stays on screen. */
    error: sub.error ?? null,
    retry: sub.restart,
    /** Saves the whole buffer, whatever the screen's filters show. */
    downloadLines: () => {
      if (logBuffer.length === 0) {
        toast.info(t("downloadEmpty"));
        return;
      }
      const filename = logFilename([
        { value: a?.slug ?? slug, fallback: "app" },
        { value: selectedPod, fallback: "pod" },
      ]);
      downloadTextFile(filename, formatLogFile(toLogLines(logBuffer)));
      toast.success(t("downloadStarted", { filename }));
    },
  };
}
