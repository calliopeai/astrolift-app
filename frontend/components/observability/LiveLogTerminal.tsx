"use client";

import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import { Loader2Icon } from "lucide-react";
import * as React from "react";

import "@xterm/xterm/css/xterm.css";

import { cn } from "@/lib/utils";

export interface LiveLogTerminalProps {
  /** AgentTask id; a new id wipes the buffer so the new run tails fresh. */
  taskId: string;
  /** Whether the run is live; an empty terminal run reads "no output". */
  running: boolean;
  /** The newest lines, a sliding window; null before the first answer. */
  lines: string[] | null;
  error: string | null;
  loading: boolean;
  /** Sizing lives on the caller: pass a height (`h-[28rem]`) or `flex-1 min-h-0`. */
  className?: string;
}

/**
 * Read-only "log theatre" for a headless (non-VNC) agent run — the terminal
 * counterpart to {@link VncViewer}, which only covers GUI agents.
 *
 * The lines come from {@link useAgentTaskLogs}, which polls while the run is
 * live. Each answer is the newest `tail` lines — a sliding window. Rather
 * than clear+rewrite the buffer every tick (which flickers and drops the
 * scrollback), we diff each new window against the previous one and write only
 * the new suffix into an xterm.js terminal (see {@link appendedTail}). Auto-
 * scroll sticks to the bottom only when the viewer is already pinned there, so a
 * manual scroll-up to read history is respected.
 *
 * Read-only: no stdin, no cursor. The xterm setup mirrors
 * {@link TerminalEmulator} (theme, fit addon, ResizeObserver) minus the
 * interactive WebSocket path.
 */
export function LiveLogTerminal({
  taskId,
  running,
  lines,
  error,
  loading,
  className,
}: LiveLogTerminalProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const termRef = React.useRef<Terminal | null>(null);
  const fitRef = React.useRef<FitAddon | null>(null);
  // The previous poll's window (its last `tail` lines). Diffed against each new
  // window so we append only genuinely-new lines rather than the whole buffer.
  const lastWindowRef = React.useRef<string[]>([]);

  // Latches true once the first line is painted so the placeholder overlay
  // clears and never flickers back on a transient empty poll. Set from the xterm
  // write callback (an external-system callback, not the effect body).
  const [hasOutput, setHasOutput] = React.useState(false);

  // One xterm per mount, reused for the run's lifetime.
  React.useEffect(() => {
    if (!containerRef.current) return;
    const term = new Terminal({
      convertEol: true,
      disableStdin: true,
      cursorBlink: false,
      scrollback: 5000,
      fontFamily:
        'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace',
      fontSize: 13,
      theme: {
        background: "#0a1310",
        foreground: "#eaf6ef",
        // Read-only surface: paint the (unused) cursor into the background so it
        // doesn't read as a live interactive prompt.
        cursor: "#0a1310",
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    // Defer the first fit so the parent has laid out its real size.
    requestAnimationFrame(() => {
      try {
        fit.fit();
      } catch {
        // jsdom or a zero-size parent — best-effort.
      }
    });
    termRef.current = term;
    fitRef.current = fit;
    return () => {
      term.dispose();
      termRef.current = null;
      fitRef.current = null;
    };
  }, []);

  // Refit on container resize (mirrors TerminalEmulator, minus the PTY resize).
  React.useEffect(() => {
    if (!containerRef.current) return;
    const ob = new ResizeObserver(() => {
      try {
        fitRef.current?.fit();
      } catch {
        // best-effort
      }
    });
    ob.observe(containerRef.current);
    return () => ob.disconnect();
  }, []);

  // Re-key on task change: the component is normally re-mounted per task, but
  // guard the in-place swap — wipe the buffer + diff state so we re-tail fresh.
  React.useEffect(() => {
    lastWindowRef.current = [];
    termRef.current?.reset();
  }, [taskId]);

  // Append-only render: diff the new window against the last and write just the
  // new suffix, keeping the viewer's scroll position unless they're at bottom.
  React.useEffect(() => {
    const term = termRef.current;
    if (!term || !lines || lines.length === 0) return;
    const additions = appendedTail(lastWindowRef.current, lines);
    lastWindowRef.current = lines;
    if (additions.length === 0) return;
    const atBottom = isAtBottom(term);
    // Scroll + reveal happen once xterm has parsed the chunk. setHasOutput is
    // idempotent — React bails out on the unchanged value after the first line.
    term.write(additions.join("\r\n") + "\r\n", () => {
      // The chunk can finish parsing after unmount; a disposed xterm throws.
      if (termRef.current !== term) return;
      if (atBottom) term.scrollToBottom();
      setHasOutput(true);
    });
  }, [lines]);

  return (
    <div className={cn("flex flex-col gap-2", className)}>
      {error && (
        <div
          className="border-danger-border bg-danger/10 text-danger-fg rounded-md border px-2.5 py-1.5 text-xs"
          role="status"
        >
          Couldn&apos;t load logs: {error}
        </div>
      )}
      <div className="relative min-h-0 flex-1">
        <div
          ref={containerRef}
          role="log"
          aria-label="Agent run logs"
          // exact terminal-canvas background — must match the xterm
          // theme.background literal above; not tokenizable.
          // eslint-disable-next-line astrolift/no-raw-design-values
          className="h-full w-full rounded-md border bg-[#0a1310] p-2 [&_.xterm-viewport]:!overflow-y-auto"
        />
        {!hasOutput && !error && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
            {running || loading ? (
              <span className="text-muted-foreground inline-flex items-center gap-2 text-xs">
                <Loader2Icon className="size-3.5 animate-spin" />
                Waiting for output…
              </span>
            ) : (
              <span className="text-muted-foreground text-xs">
                No output was recorded for this run.
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The new lines in `next` relative to `prev`, where both are trailing windows of
 * the same growing log (`agentTaskLogs` returns the newest `tail` lines each
 * poll). Between polls the window grows at the tail and, once it exceeds `tail`,
 * drops lines off the head. The new lines are everything past the overlap: the
 * largest `k` where prev's last-k lines equal next's first-k lines makes
 * `next.slice(k)` new. No overlap (a burst larger than `tail` between polls)
 * falls back to the whole window.
 */
function appendedTail(prev: string[], next: string[]): string[] {
  const max = Math.min(prev.length, next.length);
  for (let k = max; k > 0; k--) {
    let match = true;
    for (let i = 0; i < k; i++) {
      if (prev[prev.length - k + i] !== next[i]) {
        match = false;
        break;
      }
    }
    if (match) return next.slice(k);
  }
  return next.slice();
}

/** Whether the viewport is pinned to the bottom (nothing scrolled out below). */
function isAtBottom(term: Terminal): boolean {
  const buf = term.buffer.active;
  return buf.viewportY >= buf.baseY;
}
