"use client";

import { Download, Loader2, Pause, Play } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

import { HEALTH_COLOR, MOTION_CLASS } from "../core/semantics";
import type { LegendItem } from "../core/VizLegend";
import type { RunTimeline } from "../core/workflow-model";

import {
  STAGE_HEALTH,
  formatClock,
  formatDuration,
  placeRun,
  slowestStage,
  stageIndexAt,
  stepBoundary,
  type PlacedStage,
} from "./replay";
import { canExportVideo, downloadBlob, exportGif, exportVideo, readPalette } from "./replay-export";

/**
 * Run replay (spec 44 viz addendum, workflow styles): a run scrubbed like a
 * time-lapse. Each stage is a segment as wide as it really took, so a stall
 * reads as a long block. The playhead is the only moving thing and it moves
 * because replay time is passing; stages light as it enters them and settle
 * to their final status colour once it leaves.
 *
 * Reduced motion: no auto-play and no transitions. The picture opens at the
 * end of the run (every stage in its final colour, the same information as a
 * finished replay) and scrubbing or the arrow keys jump the playhead
 * instantly.
 *
 * The playhead position is written to the DOM from refs on each animation
 * frame; React state changes only when the playhead crosses a stage boundary.
 */

export interface RunReplayProps {
  timeline: RunTimeline;
  motion: "full" | "reduced";
  /** For a run still going: where the track ends. Defaults to the latest timestamp. */
  now?: number;
  /** A stage segment was chosen (click, or Enter, which also jumps the playhead to its start). */
  onSelectStage?: (stageId: string) => void;
  className?: string;
}

export const RUN_REPLAY_LEGEND: LegendItem[] = [
  { glyph: "bar", color: "var(--muted-foreground)", label: "Segment width is real duration" },
  { glyph: "flow", color: "var(--foreground)", label: "Playhead moving: replaying" },
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Stage under the playhead" },
  { glyph: "dot", color: HEALTH_COLOR.ok, label: "Succeeded" },
  { glyph: "glow", color: HEALTH_COLOR.failing, label: "Failed" },
  { glyph: "ring", color: HEALTH_COLOR.degraded, label: "Gate waiting (breathes)" },
  { glyph: "ring", color: HEALTH_COLOR.idle, label: "Not reached or skipped" },
];

const SPEEDS = [1, 10, 60] as const;
type Speed = (typeof SPEEDS)[number];

type SegmentState = "ahead" | "active" | "settled" | "skipped";

const mix = (color: string, pct: number) => `color-mix(in oklab, ${color} ${pct}%, transparent)`;

function segmentStyle(p: PlacedStage, state: SegmentState): React.CSSProperties {
  const c = HEALTH_COLOR[STAGE_HEALTH[p.stage.status]];
  if (state === "skipped")
    return { background: "transparent", borderColor: "var(--border)", borderStyle: "dashed" };
  if (state === "ahead")
    return { background: mix("var(--muted-foreground)", 8), borderColor: "var(--border)" };
  // Active: the stage the replay is in, as it was at that moment: a stage at
  // work (ok) or a gate holding for a decision (degraded). Its outcome shows
  // only once the playhead leaves it.
  if (state === "active") {
    const live = p.stage.kind === "gate" ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok;
    return {
      background: mix(live, 14),
      borderColor: live,
      boxShadow: `0 0 12px ${mix(live, 55)}`,
    };
  }
  const failed = p.stage.status === "failed";
  return {
    background: mix(c, failed ? 30 : 20),
    borderColor: mix(c, failed ? 100 : 60),
    boxShadow: failed ? `0 0 10px ${mix(c, 50)}` : undefined,
  };
}

function gateNote(p: PlacedStage, reached: boolean, done: boolean): string | null {
  if (p.stage.kind !== "gate") return null;
  if (!reached) return null;
  if (!done || p.stage.status === "waiting") return "waiting for approval";
  if (!p.stage.decidedBy) return null;
  return `${p.stage.status === "failed" ? "denied" : "approved"} by ${p.stage.decidedBy}`;
}

function describe(timeline: RunTimeline, total: number): string {
  const n = timeline.stages.length;
  const failed = timeline.stages.filter((s) => s.status === "failed").length;
  const waiting = timeline.stages.filter((s) => s.status === "waiting").length;
  const span =
    timeline.finishedAt === null
      ? `running for ${formatDuration(total)}`
      : `took ${formatDuration(total)}`;
  const parts = [`${n} stage${n === 1 ? "" : "s"}`];
  if (failed) parts.push(`${failed} failed`);
  if (waiting) parts.push(`${waiting} waiting at a gate`);
  return `Run ${timeline.label}: ${parts.join(", ")}, ${span}`;
}

export function RunReplay({ timeline, motion, now, onSelectStage, className }: RunReplayProps) {
  const run = React.useMemo(() => placeRun(timeline, now), [timeline, now]);
  const slow = React.useMemo(() => slowestStage(run), [run]);
  const reduced = motion === "reduced";

  const runRef = React.useRef(run);
  // Where the playhead opens: the end (the settled picture) under reduced
  // motion, the start otherwise. Later positions live in tRef, off React state.
  const [t0] = React.useState(() => (reduced ? run.total : 0));
  const tRef = React.useRef(t0);
  /** Pinned to the end: a live run's growth carries the playhead with it. */
  const followRef = React.useRef(reduced);
  const dragRef = React.useRef(false);
  const trackRef = React.useRef<HTMLDivElement>(null);
  const headRef = React.useRef<HTMLDivElement>(null);
  const fillRef = React.useRef<HTMLDivElement>(null);
  const clockRef = React.useRef<HTMLSpanElement>(null);
  const rootRef = React.useRef<HTMLDivElement>(null);
  const [exporting, setExporting] = React.useState<"gif" | "video" | null>(null);

  const [playRequested, setPlaying] = React.useState(true);
  // Reduced motion never auto-plays.
  const playing = playRequested && !reduced;
  const [speed, setSpeed] = React.useState<Speed>(60);
  const speedRef = React.useRef<Speed>(speed);
  const [view, setView] = React.useState(() => {
    const idx = stageIndexAt(run, t0);
    return { idx, done: idx >= 0 && t0 >= run.stages[idx].end };
  });
  const viewRef = React.useRef(view);

  const paint = React.useCallback(() => {
    const r = runRef.current;
    const t = tRef.current;
    const pct = r.total > 0 ? (t / r.total) * 100 : 0;
    const idx = stageIndexAt(r, t);
    const active = idx >= 0 ? r.stages[idx] : null;
    const done = !!active && t >= active.end;
    if (headRef.current) {
      headRef.current.style.left = `${pct}%`;
      headRef.current.setAttribute("aria-valuenow", String(Math.round(t / 1000)));
      headRef.current.setAttribute(
        "aria-valuetext",
        `${formatClock(t)} elapsed${active ? `, ${active.stage.name}` : ""}`
      );
    }
    if (fillRef.current) {
      // The part of the active stage already replayed.
      const show = !!active && !done && r.total > 0;
      fillRef.current.style.display = show ? "block" : "none";
      if (show) {
        fillRef.current.style.background = mix(
          active.stage.kind === "gate" ? HEALTH_COLOR.degraded : HEALTH_COLOR.ok,
          16
        );
        fillRef.current.style.left = `${(active.start / r.total) * 100}%`;
        fillRef.current.style.width = `${((t - active.start) / r.total) * 100}%`;
      }
    }
    if (clockRef.current) clockRef.current.textContent = formatClock(t);
    if (viewRef.current.idx !== idx || viewRef.current.done !== done) {
      viewRef.current = { idx, done };
      setView(viewRef.current);
    }
  }, []);

  const seek = React.useCallback(
    (t: number) => {
      const total = runRef.current.total;
      tRef.current = Math.max(0, Math.min(total, t));
      followRef.current = tRef.current >= total - 1;
      paint();
    },
    [paint]
  );

  // A new or growing timeline: keep the playhead in range, riding the end if pinned.
  React.useLayoutEffect(() => {
    runRef.current = run;
    if (followRef.current || tRef.current > run.total) tRef.current = run.total;
    paint();
  }, [run, paint]);

  React.useEffect(() => {
    speedRef.current = speed;
  }, [speed]);

  React.useEffect(() => {
    if (!playing || reduced || typeof window.requestAnimationFrame !== "function") return;
    let raf = 0;
    let last: number | null = null;
    const tick = (ts: number) => {
      if (last !== null && !dragRef.current) {
        const total = runRef.current.total;
        const t = tRef.current + (ts - last) * speedRef.current;
        if (t >= total) {
          seek(total);
          setPlaying(false);
          return;
        }
        tRef.current = t;
        followRef.current = false;
        paint();
      }
      last = ts;
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [playing, reduced, paint, seek]);

  const togglePlay = () => {
    if (reduced) return;
    if (!playing && tRef.current >= runRef.current.total - 1) seek(0);
    setPlaying((p) => !p);
  };

  const step = (dir: 1 | -1) => {
    setPlaying(false);
    seek(stepBoundary(runRef.current, tRef.current, dir));
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const target = e.target as HTMLElement;
    if (e.key === " " && !target.closest("button")) {
      e.preventDefault();
      togglePlay();
    } else if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
      e.preventDefault();
      step(e.key === "ArrowRight" ? 1 : -1);
    } else if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setPlaying(false);
      seek(e.key === "Home" ? 0 : runRef.current.total);
    }
  };

  const seekFromPointer = (clientX: number) => {
    const el = trackRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0) return;
    seek(((clientX - rect.left) / rect.width) * runRef.current.total);
  };

  const selectStage = (p: PlacedStage) => {
    setPlaying(false);
    seek(p.start);
    onSelectStage?.(p.stage.id);
  };

  const runExport = async (kind: "gif" | "video") => {
    if (!rootRef.current || exporting) return;
    setExporting(kind);
    try {
      const colors = readPalette(rootRef.current);
      const opts = { label: timeline.label };
      const blob =
        kind === "gif" ? await exportGif(run, colors, opts) : await exportVideo(run, colors, opts);
      downloadBlob(blob, `${timeline.runId}-replay.${kind === "gif" ? "gif" : "webm"}`);
    } catch (e) {
      toast.error(`Couldn't export the replay: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setExporting(null);
    }
  };

  const { idx, done } = view;
  const active = idx >= 0 ? run.stages[idx] : null;
  const total = run.total;
  const pctOf = (ms: number) => (total > 0 ? (ms / total) * 100 : 0);
  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const transition = reduced
    ? undefined
    : "background-color 240ms, border-color 240ms, box-shadow 240ms";

  let status: string;
  if (!active) status = "Queued";
  else if (!done) status = active.stage.kind === "gate" ? "waiting for approval" : "running";
  else status = active.stage.status;
  const note = active ? gateNote(active, true, done) : null;
  const activeColor = active ? HEALTH_COLOR[STAGE_HEALTH[active.stage.status]] : undefined;

  return (
    <div
      ref={rootRef}
      data-motion={motion}
      role="figure"
      aria-label={describe(timeline, total)}
      onKeyDown={onKeyDown}
      className={cn("bg-card flex min-w-0 flex-col gap-3 p-4 text-sm", className)}
    >
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex min-w-0 items-baseline gap-2">
          <span className="truncate font-medium" title={timeline.label}>
            {timeline.label}
          </span>
          <span className="text-muted-foreground font-mono text-xs">{timeline.runId}</span>
        </div>
        <div className="font-mono text-xs tabular-nums">
          <span ref={clockRef}>{formatClock(t0)}</span>
          <span className="text-muted-foreground"> / {formatClock(total)}</span>
        </div>
      </div>

      <div className="flex min-w-0 items-center gap-2 text-xs" aria-live="polite">
        <span
          aria-hidden
          className="size-2 shrink-0 rounded-full"
          style={{
            background: !active
              ? "var(--border)"
              : done
                ? activeColor
                : active.stage.kind === "gate"
                  ? HEALTH_COLOR.degraded
                  : HEALTH_COLOR.ok,
          }}
        />
        <span className="truncate">
          {active ? (
            <>
              <span className="font-medium" title={active.stage.name}>
                {active.stage.name}
              </span>
              <span className="text-muted-foreground"> · {status}</span>
              {note && <span className="text-muted-foreground font-mono"> · {note}</span>}
            </>
          ) : (
            <span className="text-muted-foreground">{status}</span>
          )}
        </span>
      </div>

      <div
        ref={trackRef}
        className="relative h-14 cursor-pointer touch-none select-none"
        onPointerDown={(e) => {
          dragRef.current = true;
          setPlaying(false);
          e.currentTarget.setPointerCapture?.(e.pointerId);
          headRef.current?.focus();
          seekFromPointer(e.clientX);
        }}
        onPointerMove={(e) => {
          if (dragRef.current) seekFromPointer(e.clientX);
        }}
        onPointerUp={() => {
          dragRef.current = false;
        }}
        onPointerCancel={() => {
          dragRef.current = false;
        }}
      >
        {run.stages.map((p, i) => {
          const state: SegmentState =
            p.stage.status === "skipped"
              ? "skipped"
              : i < idx || (i === idx && done)
                ? "settled"
                : i === idx
                  ? "active"
                  : "ahead";
          const reached = state === "active" || state === "settled";
          // A gate breathes while it holds: live, or while the replay is inside it.
          const holding =
            p.stage.kind === "gate" &&
            (state === "active" || (state === "settled" && p.stage.status === "waiting"));
          const gate = gateNote(p, reached, state === "settled");
          const dur = formatDuration(p.end - p.start);
          const tip = [
            `${p.stage.name} (${p.stage.kind}): ${p.stage.status}, ${dur}`,
            p.stage.decidedBy ? `decided by ${p.stage.decidedBy}` : null,
          ]
            .filter(Boolean)
            .join(", ");
          return (
            <div
              key={p.stage.id}
              data-stage={p.stage.id}
              role="button"
              tabIndex={0}
              aria-label={tip}
              title={tip}
              onClick={() => onSelectStage?.(p.stage.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.stopPropagation();
                  selectStage(p);
                }
              }}
              className={cn(
                "focus-visible:ring-ring absolute top-0 flex h-full flex-col justify-center overflow-hidden rounded-sm border px-1.5 outline-none focus-visible:z-10 focus-visible:ring-2",
                holding && MOTION_CLASS.breathe
              )}
              style={{
                left: `${pctOf(p.start)}%`,
                width: `${pctOf(p.end - p.start)}%`,
                minWidth: 2,
                transition,
                ...segmentStyle(p, state),
              }}
            >
              <span
                className={cn(
                  "truncate text-xs",
                  reached ? "text-foreground" : "text-muted-foreground",
                  p.stage.kind === "gate" && "font-medium"
                )}
              >
                {p.stage.name}
              </span>
              <span className="text-muted-foreground text-2xs truncate font-mono">
                {gate ?? (reached || state === "skipped" ? dur : "")}
                {state === "skipped" ? " skipped" : ""}
              </span>
            </div>
          );
        })}
        <div
          ref={fillRef}
          aria-hidden
          className="pointer-events-none absolute top-0 h-full"
          style={{ display: "none" }}
        />
        <div
          ref={headRef}
          role="slider"
          tabIndex={0}
          aria-label="Replay position"
          aria-valuemin={0}
          aria-valuemax={Math.round(total / 1000)}
          aria-valuenow={Math.round(t0 / 1000)}
          className="focus-visible:ring-ring pointer-events-none absolute -top-1 -bottom-1 z-20 w-0.5 -translate-x-1/2 outline-none focus-visible:ring-2"
          style={{ left: `${pctOf(t0)}%`, background: "var(--foreground)" }}
        >
          <span
            aria-hidden
            className="absolute -top-1 left-1/2 size-2 -translate-x-1/2 rounded-full"
            style={{ background: "var(--foreground)" }}
          />
        </div>
      </div>

      <div className="text-muted-foreground text-2xs relative h-4 font-mono" aria-hidden>
        {ticks.map((f) => (
          <span
            key={f}
            className="absolute top-0"
            style={{
              left: `${f * 100}%`,
              transform: f === 0 ? undefined : f === 1 ? "translateX(-100%)" : "translateX(-50%)",
            }}
          >
            {formatClock(total * f)}
          </span>
        ))}
      </div>

      {slow && (
        <p className="text-xs">
          <span className="text-muted-foreground">Slowest stage: </span>
          <span className="font-medium">{slow.stage.name}</span> took{" "}
          <span className="font-mono">{formatDuration(slow.ms)}</span>,{" "}
          <span className="font-mono">{Math.round(slow.share * 100)}%</span> of the run
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          onClick={togglePlay}
          disabled={reduced}
          title={reduced ? "Reduced motion: drag the playhead or use the arrow keys" : undefined}
          aria-label={playing ? "Pause replay" : "Play replay"}
        >
          {playing ? <Pause aria-hidden /> : <Play aria-hidden />}
          {playing ? "Pause" : "Play"}
        </Button>
        <div role="radiogroup" aria-label="Replay speed" className="flex rounded-md border p-0.5">
          {SPEEDS.map((s) => (
            <Button
              key={s}
              type="button"
              size="xs"
              role="radio"
              aria-checked={speed === s}
              variant={speed === s ? "secondary" : "ghost"}
              className="font-mono"
              onClick={() => setSpeed(s)}
            >
              {s}x
            </Button>
          ))}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="ml-auto"
              disabled={exporting !== null || total <= 0}
            >
              {exporting ? (
                <Loader2 aria-hidden className="animate-spin" />
              ) : (
                <Download aria-hidden />
              )}
              {exporting === "video" ? "Recording…" : exporting === "gif" ? "Encoding…" : "Export"}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onSelect={() => void runExport("gif")}>
              GIF (6s time-lapse)
            </DropdownMenuItem>
            <DropdownMenuItem disabled={!canExportVideo()} onSelect={() => void runExport("video")}>
              Video, WebM (6s time-lapse)
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
