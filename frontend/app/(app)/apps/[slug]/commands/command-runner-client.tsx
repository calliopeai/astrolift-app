"use client";

import { useQuery } from "@apollo/client/react";
import { PlayIcon, SquareIcon, TerminalIcon, XIcon } from "lucide-react";
import * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  LIST_CONTAINERS,
  LIST_WORKLOADS,
} from "@/graphql/registry/registry.queries";
import type {
  AstroliftContainer,
  AstroliftWorkload,
} from "@/graphql/registry/registry.types";

import { AppTabs } from "../components/app-tabs";

interface WorkloadsResp {
  astroliftWorkloads: AstroliftWorkload[];
}
interface ContainersResp {
  astroliftContainers: AstroliftContainer[];
}

type ConnState = "idle" | "connecting" | "open" | "closed";

interface OutputChunk {
  channel: "stdout" | "stderr" | "system";
  text: string;
}

const HISTORY_KEY_PREFIX = "astrolift:cmd-history:";

export function CommandRunnerClient({ slug }: { slug: string }) {
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
  const [containerName, setContainerName] = React.useState("");
  React.useEffect(() => {
    if (!containerName && containerList.length > 0) {
      const primary = containerList.find((c) => c.isPrimary) ?? containerList[0];
      setContainerName(primary.name);
    }
  }, [containerName, containerList]);

  const [command, setCommand] = React.useState("");
  const [output, setOutput] = React.useState<OutputChunk[]>([]);
  const [connState, setConnState] = React.useState<ConnState>("idle");
  const [exitCode, setExitCode] = React.useState<number | null>(null);
  const wsRef = React.useRef<WebSocket | null>(null);
  const outputRef = React.useRef<HTMLPreElement | null>(null);

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

  // Auto-scroll output to bottom.
  React.useEffect(() => {
    const el = outputRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output]);

  function appendChunk(chunk: OutputChunk) {
    setOutput((prev) => [...prev, chunk]);
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

  return (
    <PageShell
      title="Run command"
      description={
        <span className="text-muted-foreground font-mono text-xs">
          Open a one-shot exec session against a container in {slug}. Each
          invocation runs sh -c &lt;command&gt; in the chosen container; stdout +
          stderr stream live below.
        </span>
      }
    >
      <AppTabs slug={slug} active="deployments" />
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <TerminalIcon className="size-4" />
            Target
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="cmd-workload">Workload</Label>
            <Select value={workloadSlug} onValueChange={setWorkloadSlug} disabled={running}>
              <SelectTrigger id="cmd-workload">
                <SelectValue placeholder="Select workload" />
              </SelectTrigger>
              <SelectContent>
                {workloads.map((w) => (
                  <SelectItem key={w.id} value={w.slug}>
                    <span className="font-mono">{w.slug}</span>
                    <span className="text-muted-foreground ml-2 text-xs">
                      {w.kind}
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="cmd-container">Container</Label>
            <Select
              value={containerName}
              onValueChange={setContainerName}
              disabled={running || containerList.length === 0}
            >
              <SelectTrigger id="cmd-container">
                <SelectValue placeholder="Select container" />
              </SelectTrigger>
              <SelectContent>
                {containerList.map((c) => (
                  <SelectItem key={c.id} value={c.name}>
                    <span className="font-mono">{c.name}</span>
                    {c.isPrimary && (
                      <Badge variant="outline" className="ml-2 text-[10px]">
                        primary
                      </Badge>
                    )}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Command</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex items-stretch gap-2">
            <Input
              value={command}
              onChange={(e) => setCommand(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !running) {
                  e.preventDefault();
                  handleRun();
                }
              }}
              placeholder="ls -la /tmp"
              disabled={running}
              spellCheck={false}
              className="font-mono"
            />
            {running ? (
              <Button onClick={handleClose} variant="destructive">
                <SquareIcon className="size-3.5" />
                Stop
              </Button>
            ) : (
              <Button onClick={handleRun} disabled={!command.trim() || !workloadSlug}>
                <PlayIcon className="size-3.5" />
                Run
              </Button>
            )}
          </div>
          {history.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-muted-foreground text-xs uppercase tracking-wide">
                History
              </span>
              {history.slice(0, 8).map((h, i) => (
                <button
                  key={`${h}-${i}`}
                  onClick={() => setCommand(h)}
                  disabled={running}
                  className="rounded border bg-muted/40 px-2 py-0.5 font-mono text-[11px] hover:bg-muted"
                >
                  {h.length > 40 ? h.slice(0, 40) + "…" : h}
                </button>
              ))}
              <button
                onClick={() => {
                  setHistory([]);
                  try {
                    window.localStorage.removeItem(historyKey);
                  } catch {
                    // ignore
                  }
                }}
                className="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-[11px]"
              >
                <XIcon className="size-3" />
                clear
              </button>
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between space-y-0">
          <CardTitle className="text-base">Output</CardTitle>
          <div className="flex items-center gap-2 text-xs">
            <Badge
              variant={
                connState === "open"
                  ? "secondary"
                  : connState === "closed" && exitCode !== null && exitCode !== 0
                    ? "destructive"
                    : "outline"
              }
              className="capitalize"
            >
              {connState}
            </Badge>
            {exitCode !== null && (
              <span className="text-muted-foreground font-mono">
                exit {exitCode}
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent className="p-0">
          <pre
            ref={outputRef}
            className="bg-muted/30 max-h-[480px] min-h-[180px] overflow-auto p-4 font-mono text-xs leading-relaxed"
          >
            {output.length === 0 ? (
              <span className="text-muted-foreground italic">
                No output yet. Pick a workload + container, type a command, and Run.
              </span>
            ) : (
              output.map((c, i) => (
                <span
                  key={i}
                  className={
                    c.channel === "stderr"
                      ? "text-destructive"
                      : c.channel === "system"
                        ? "text-muted-foreground italic"
                        : ""
                  }
                >
                  {c.text}
                </span>
              ))
            )}
          </pre>
        </CardContent>
      </Card>
    </PageShell>
  );
}
