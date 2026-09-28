"use client";

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
  /** The app tab bar. */
  tabs?: React.ReactNode;
}

/** App > Run command: a one-shot `sh -c` exec against one container. */
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
  tabs,
}: CommandRunnerScreenProps) {
  const outputRef = React.useRef<HTMLPreElement | null>(null);

  // Auto-scroll output to bottom.
  React.useEffect(() => {
    const el = outputRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [output]);

  return (
    <PageShell
      title="Run command"
      description={
        <span className="text-muted-foreground font-mono text-xs">
          Open a one-shot exec session against a container in {slug}. Each invocation runs sh -c
          &lt;command&gt; in the chosen container; stdout + stderr stream live below.
        </span>
      }
    >
      {tabs}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <TerminalIcon className="size-4" />
            Target
          </CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="cmd-workload">Workload</Label>
            <Select value={workloadSlug} onValueChange={onWorkloadChange} disabled={running}>
              <SelectTrigger id="cmd-workload">
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
          <div className="space-y-2">
            <Label htmlFor="cmd-container">Container</Label>
            <Select
              value={containerName}
              onValueChange={onContainerChange}
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
                      <Badge variant="outline" className="text-2xs ml-2">
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
              onChange={(e) => onCommandChange(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !running) {
                  e.preventDefault();
                  onRun();
                }
              }}
              placeholder="ls -la /tmp"
              disabled={running}
              spellCheck={false}
              className="font-mono"
            />
            {running ? (
              <Button onClick={onStop} variant="destructive">
                <SquareIcon className="size-3.5" />
                Stop
              </Button>
            ) : (
              <Button onClick={onRun} disabled={!command.trim() || !workloadSlug}>
                <PlayIcon className="size-3.5" />
                Run
              </Button>
            )}
          </div>
          {history.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-muted-foreground text-xs tracking-wide uppercase">History</span>
              {history.slice(0, 8).map((h, i) => (
                <button
                  key={`${h}-${i}`}
                  onClick={() => onCommandChange(h)}
                  disabled={running}
                  className="bg-muted/40 text-2xs hover:bg-muted rounded border px-2 py-0.5 font-mono"
                >
                  {h.length > 40 ? h.slice(0, 40) + "…" : h}
                </button>
              ))}
              <button
                onClick={onClearHistory}
                className="text-muted-foreground hover:text-foreground text-2xs inline-flex items-center gap-1"
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
              <span className="text-muted-foreground font-mono">exit {exitCode}</span>
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
