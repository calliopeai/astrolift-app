"use client";

import { useQuery } from "@apollo/client/react";
import * as React from "react";

import { downloadTextFile, logFilename } from "@/components/screens/apps/deployments/app-log-lines";
import { LIST_CONTAINERS, LIST_WORKLOADS } from "@/graphql/registry/registry.queries";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";

interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}

export type ConnState = "idle" | "connecting" | "open" | "closed";

export interface OutputChunk {
  channel: "stdout" | "stderr" | "system";
  text: string;
  /** Epoch ms the chunk arrived; the output pane shows it as the line time. */
  ts: number;
}

const HISTORY_KEY_PREFIX = "astrolift:cmd-history:";

/**
 * The one-shot exec runner: workload + container pickers, the WS exec
 * session, its streamed output, and the per-app command history.
 */
export function useCommandRunner(slug: string) {
  const wls = useQuery<WorkloadsResp>(LIST_WORKLOADS, {
    variables: { appSlug: slug },
  });
  const workloads = wls.data?.astroliftWorkloads ?? [];

  const [workloadSlug, setWorkloadSlug] = React.useState("");
  React.useEffect(() => {
    if (!workloadSlug && workloads.length > 0) {
      const primary = workloads.find((w) => w.isPublic) ?? workloads[0];
      setWorkloadSlug(primary.slug);
    }
  }, [workloadSlug, workloads]);

  const containers = useQuery<ContainersResp>(LIST_CONTAINERS, {
    variables: { workloadSlug },
    skip: !workloadSlug,
  });
  const containerList = containers.data?.astroliftContainers ?? [];
  const [containerChoice, setContainerChoice] = React.useState({ workloadSlug: "", name: "" });
  const containerName =
    containerChoice.workloadSlug === workloadSlug &&
    containerList.some((c) => c.name === containerChoice.name)
      ? containerChoice.name
      : ((containerList.find((c) => c.isPrimary) ?? containerList[0])?.name ?? "");
  const setContainerName = (name: string) => setContainerChoice({ workloadSlug, name });

  const [command, setCommand] = React.useState("");
  const [output, setOutput] = React.useState<OutputChunk[]>([]);
  const [connState, setConnState] = React.useState<ConnState>("idle");
  const [exitCode, setExitCode] = React.useState<number | null>(null);
  const wsRef = React.useRef<WebSocket | null>(null);

  // Per-app shell history persisted to localStorage.
  const historyKey = `${HISTORY_KEY_PREFIX}${slug}`;
  const [history, setHistory] = React.useState<string[]>([]);
  React.useEffect(() => {
    try {
      const raw = window.localStorage.getItem(historyKey);
      if (raw) setHistory(JSON.parse(raw));
    } catch {
      // localStorage may be unavailable (private browsing); ignore.
    }
  }, [historyKey]);

  function pushHistory(cmd: string) {
    const next = [cmd, ...history.filter((h) => h !== cmd)].slice(0, 20);
    setHistory(next);
    try {
      window.localStorage.setItem(historyKey, JSON.stringify(next));
    } catch {
      // ignore
    }
  }

  function clearHistory() {
    setHistory([]);
    try {
      window.localStorage.removeItem(historyKey);
    } catch {
      // ignore
    }
  }

  function appendChunk(chunk: Omit<OutputChunk, "ts">) {
    setOutput((prev) => [...prev, { ...chunk, ts: Date.now() }]);
  }

  /** The run's raw output as a file, byte for byte as it streamed. */
  function downloadOutput() {
    if (output.length === 0) return;
    downloadTextFile(
      logFilename([
        { value: slug, fallback: "app" },
        { value: workloadSlug, fallback: "workload" },
        { value: containerName, fallback: "container" },
      ]),
      output.map((c) => c.text).join("")
    );
  }

  function handleClose() {
    if (wsRef.current && wsRef.current.readyState <= 1) {
      try {
        wsRef.current.send(JSON.stringify({ type: "close" }));
      } catch {
        // best-effort
      }
      wsRef.current.close();
    }
    setConnState("closed");
  }

  async function handleRun() {
    if (!workloadSlug || !containerName || !command.trim()) return;

    setOutput([]);
    setExitCode(null);
    setConnState("connecting");
    pushHistory(command);

    // Connect to /app/exec/<app>/<workload>. Same origin, so the
    // existing session cookie + tenant context flow over the WS
    // handshake automatically.
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/app/exec/${encodeURIComponent(slug)}/${encodeURIComponent(workloadSlug)}`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnState("open");
      // Bash one-shot. -c lets us pass the user's free-form string.
      const payload = {
        type: "open",
        command: ["sh", "-c", command],
        container: containerName,
      };
      ws.send(JSON.stringify(payload));
    };

    ws.onmessage = (ev) => {
      try {
        const frame = JSON.parse(ev.data);
        if (frame.type === "stdout") {
          appendChunk({ channel: "stdout", text: frame.data });
        } else if (frame.type === "stderr") {
          appendChunk({ channel: "stderr", text: frame.data });
        } else if (frame.type === "exit") {
          setExitCode(frame.code ?? 0);
          appendChunk({
            channel: "system",
            text: `\n[exit ${frame.code ?? 0}]\n`,
          });
        } else if (frame.type === "error") {
          appendChunk({
            channel: "system",
            text: `\n[error: ${frame.message ?? "unknown"}]\n`,
          });
        }
      } catch {
        // Non-JSON frames — append raw.
        appendChunk({ channel: "stdout", text: String(ev.data) });
      }
    };

    ws.onerror = () => {
      appendChunk({
        channel: "system",
        text: "\n[ws: connection error]\n",
      });
    };

    ws.onclose = (ev) => {
      setConnState("closed");
      if (ev.code !== 1000 && ev.code !== 1005) {
        appendChunk({
          channel: "system",
          text: `\n[ws closed: code ${ev.code}${ev.reason ? ` (${ev.reason})` : ""}]\n`,
        });
      }
    };
  }

  React.useEffect(() => {
    return () => {
      if (wsRef.current && wsRef.current.readyState <= 1) {
        wsRef.current.close();
      }
    };
  }, []);

  const running = connState === "connecting" || connState === "open";

  return {
    slug,
    workloads,
    workloadSlug,
    onWorkloadChange: setWorkloadSlug,
    containers: containerList,
    containerName,
    onContainerChange: setContainerName,
    command,
    onCommandChange: setCommand,
    output,
    connState,
    exitCode,
    running,
    history,
    onClearHistory: clearHistory,
    onRun: handleRun,
    onStop: handleClose,
    onDownload: downloadOutput,
  };
}
