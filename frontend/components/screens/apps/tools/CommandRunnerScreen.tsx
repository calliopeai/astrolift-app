"use client";

import { PlayIcon, SquareIcon, TerminalIcon, XIcon } from "lucide-react";
import type * as React from "react";

import { PageShell } from "@/components/PageShell";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { type LogLine, LogView } from "@/components/run/LogView";
import { StatusDot } from "@/components/StatusDot";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { AstroliftContainer, AstroliftWorkload } from "@/graphql/registry/registry.types";

import type { ConnState, OutputChunk } from "./use-command-runner";

export interface CommandRunnerScreenProps {
  slug: string;
  workloads: Pick<AstroliftWorkload, "id" | "slug" | "kind">[];
  workloadSlug: string;
  onWorkloadChange: (slug: string) => void;
  containers: Pick<AstroliftContainer, "id" | "name" | "isPrimary">[];
  containerName: string;
  onContainerChange: (name: string) => void;
  command: string;
  onCommandChange: (command: string) => void;
  output: OutputChunk[];
  connState: ConnState;
  exitCode: number | null;
  running: boolean;
  history: string[];
  onClearHistory: () => void;
  onRun: () => void;
  onStop: () => void;
  /** Saves the run's raw output. */
  onDownload: () => void;
  /** The app tab bar. */
  tabs?: React.ReactNode;
}

const CHANNEL_LEVEL: Record<OutputChunk["channel"], LogLine["level"]> = {
  stdout: undefined,
  stderr: "error",
  system: "info",
};

/**
 * Streamed chunks as log lines: a chunk may end mid-line or hold many, so
 * text is joined per channel run and split on newlines. Blank system lines
 * (the padding around `[exit 2]`) are dropped.
 */
export function outputToLines(output: OutputChunk[]): LogLine[] {
  const lines: LogLine[] = [];
  let open: { channel: OutputChunk["channel"]; text: string; ts: number } | null = null;
  const flush = () => {
    if (open && !(open.channel === "system" && open.text.trim() === "")) {
      lines.push({ ts: open.ts, message: open.text, level: CHANNEL_LEVEL[open.channel] });
    }
    open = null;
  };
  for (const chunk of output) {
    const parts = chunk.text.split("\n");
    parts.forEach((part, i) => {
      if (i > 0) flush();
      if (open && open.channel !== chunk.channel) flush();
      if (open) open.text += part;
      else if (part !== "" || i < parts.length - 1) {
        open = { channel: chunk.channel, text: part, ts: chunk.ts };
      }
    });
  }
  flush();
  return lines;
}

/**
 * Logs & metrics › Commands (spec 44 §5.2, §5.5): a one-shot `sh -c` exec
 * against one container. The target and command sit in one panel; stdout,
 * stderr and the exit stream into the shared LogView, which follows the end.
 */
export function CommandRunnerScreen({
  slug,
  workloads,
  workloadSlug,
  onWorkloadChange,
  containers: containerList,
  containerName,
  onContainerChange,
  command,
  onCommandChange,
  output,
  connState,
  exitCode,
  running,
  history,
  onClearHistory,
  onRun,
  onStop,
  onDownload,
  tabs,
}: CommandRunnerScreenProps) {
  const lines = outputToLines(output);
  const failed = connState === "closed" && exitCode !== null && exitCode !== 0;

  return (
    <PageShell
      title="Run command"
      description={
        <span className="font-mono text-xs [overflow-wrap:anywhere]">
          Open a one-shot exec session against a container in {slug}. Each invocation runs sh -c
          &lt;command&gt; in the chosen container; stdout + stderr stream live below.
        </span>
      }
    >
      {tabs}
      <PanelGrid>
        <Panel title="Command" icon={<TerminalIcon className="size-4" />}>
          <div className="flex min-w-0 flex-col gap-4">
            <div className="grid min-w-0 gap-4 sm:grid-cols-2">
              <div className="min-w-0 space-y-2">
                <Label htmlFor="cmd-workload">Workload</Label>
                <Select value={workloadSlug} onValueChange={onWorkloadChange} disabled={running}>
                  <SelectTrigger id="cmd-workload" className="w-full min-w-0">
                    <SelectValue placeholder="Select workload" />
                  </SelectTrigger>
                  <SelectContent>
                    {workloads.map((w) => (
                      <SelectItem key={w.id} value={w.slug}>
                        <span className="font-mono">{w.slug}</span>
                        <span className="text-muted-foreground ml-2 text-xs">{w.kind}</span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="min-w-0 space-y-2">
                <Label htmlFor="cmd-container">Container</Label>
                <Select
                  value={containerName}
                  onValueChange={onContainerChange}
                  disabled={running || containerList.length === 0}
                >
                  <SelectTrigger id="cmd-container" className="w-full min-w-0">
                    <SelectValue placeholder="Select container" />
                  </SelectTrigger>
                  <SelectContent>
                    {containerList.map((c) => (
                      <SelectItem key={c.id} value={c.name}>
                        <span className="font-mono">{c.name}</span>
                        {c.isPrimary && (
                          <Badge variant="outline" className="text-2xs ml-2">
                            primary
                          </Badge>
                        )}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="flex min-w-0 items-stretch gap-2">
              <Input
                value={command}
                onChange={(e) => onCommandChange(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !running) {
                    e.preventDefault();
                    onRun();
                  }
                }}
                placeholder="ls -la /tmp"
                aria-label="Command"
                disabled={running}
                spellCheck={false}
                className="min-w-0 font-mono"
              />
              {running ? (
                <Button onClick={onStop} variant="destructive">
                  <SquareIcon className="size-3.5" />
                  Stop
                </Button>
              ) : (
                <Button
                  onClick={onRun}
                  disabled={!command.trim() || !workloadSlug || !containerName}
                >
                  <PlayIcon className="size-3.5" />
                  Run
                </Button>
              )}
            </div>

            {history.length > 0 && (
              <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                <span className="text-muted-foreground text-xs tracking-wide uppercase">
                  History
                </span>
                {history.slice(0, 8).map((h, i) => (
                  <button
                    key={`${h}-${i}`}
                    type="button"
                    onClick={() => onCommandChange(h)}
                    disabled={running}
                    title={h}
                    className="bg-muted/40 text-2xs hover:bg-muted max-w-full min-w-0 truncate rounded border px-2 py-0.5 font-mono"
                  >
                    {h.length > 40 ? h.slice(0, 40) + "…" : h}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={onClearHistory}
                  className="text-muted-foreground hover:text-foreground text-2xs inline-flex items-center gap-1"
                >
                  <XIcon className="size-3" />
                  clear
                </button>
              </div>
            )}
          </div>
        </Panel>

        <LogView
          title="Output"
          className="col-span-12"
          lines={lines}
          onDownload={onDownload}
          emptyHint="No output yet. Pick a workload + container, type a command, and Run."
          actions={
            <span className="flex items-center gap-2 text-xs">
              <StatusDot
                status={
                  connState === "open" || connState === "connecting"
                    ? "pending"
                    : failed
                      ? "error"
                      : connState === "closed"
                        ? "ok"
                        : "muted"
                }
              />
              <span className="capitalize">{connState}</span>
              {exitCode !== null && (
                <span
                  className={
                    failed ? "text-danger-fg font-mono" : "text-muted-foreground font-mono"
                  }
                >
                  exit {exitCode}
                </span>
              )}
            </span>
          }
        />
      </PanelGrid>
    </PageShell>
  );
}
