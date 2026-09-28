"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

import type { AppNode, AppSnapshot, AppViewProps } from "../../core/app-model";
import { HEALTH_COLOR, HEALTH_GLOWS, MOTION_CLASS, flowDuration } from "../../core/semantics";
import type { LegendItem } from "../../core/VizLegend";

import {
  EVENT_WINDOW_MS,
  edgeFailing,
  formatCountdown,
  layerNodes,
  nextRun,
  parseSchedule,
  pastRuns,
  roleSections,
  summarize,
  truncate,
} from "./layouts-b";
import {
  FOCUS_RING,
  Flash,
  HealthDot,
  LoadBar,
  fmtRps,
  healthLabel,
  selectProps,
  tint,
} from "./layouts-b-parts";

/**
 * Jobs and everything else without a front door (spec 44 viz addendum, auto
 * layout for the scheduled, task, workflow and mixed topologies). One
 * component, four structures:
 * - scheduled: a next-run countdown per schedule and a strip of past slots.
 * - task: a single run card.
 * - workflow: the steps as a small transit line.
 * - mixed (and anything else): a grid of sections, one per workload role.
 *
 * Motion, and what it means:
 * - A running job or step glows (idle stays dark); a failing one flickers.
 * - Workflow segments flow with their rate; red over 5% errors.
 * - In mixed, an invoked function or acting agent ripples once.
 * - The countdown is text, recomputed from the snapshot's clock.
 *
 * Reduced motion: no flicker, ripple or flow. Failing is still red and
 * labelled, segments are solid with thickness for rate, and a recent event
 * keeps a still ring dimmed by its age.
 */

export const JOBS_LAYOUT_LEGEND: LegendItem[] = [
  { glyph: "glow", color: HEALTH_COLOR.ok, label: "Running: glows" },
  { glyph: "flicker", color: HEALTH_COLOR.failing, label: "Failing job or step" },
  { glyph: "bar", color: HEALTH_COLOR.ok, label: "Past run succeeded" },
  { glyph: "bar", color: HEALTH_COLOR.failing, label: "Past run failed" },
  { glyph: "ring", color: "var(--muted-foreground)", label: "Scheduled slot with no run record" },
  { glyph: "flow", color: HEALTH_COLOR.ok, label: "Step traffic: faster is busier" },
  { glyph: "ring", color: HEALTH_COLOR.ok, label: "Invoked or acted just now (mixed)" },
  { glyph: "dot", color: HEALTH_COLOR.idle, label: "Idle: stays dark" },
];

/** A finished run, for the scheduled history strip. The snapshot carries none. */
export interface JobRun {
  jobId: string;
  at: number;
  ok: boolean;
}

const HISTORY = 14;
const ACTIVE_LOAD = 0.05;

export type JobsLayoutProps = AppViewProps & {
  diagram: React.ReactNode;
  /** Past runs per job; without them the history strip shows slots only. */
  runs?: JobRun[];
};

function running(n: AppNode): boolean {
  return n.health !== "failing" && n.health !== "idle" && n.load >= ACTIVE_LOAD;
}

function glow(n: AppNode): string | undefined {
  if (!HEALTH_GLOWS[n.health] || n.health === "idle") return undefined;
  if (n.health === "ok" && !running(n)) return undefined;
  return `0 0 10px ${tint(HEALTH_COLOR[n.health], 45)}`;
}

export function JobsLayout(props: JobsLayoutProps) {
  const { snapshot, motion, className, diagram } = props;
  const t = snapshot.topology;
  const label = `${snapshot.app.name}: ${summarize(
    snapshot.nodes.filter((n) => n.role !== "data" && n.role !== "external"),
    "workloads"
  )}`;
  const body =
    t === "scheduled" ? (
      <ScheduledView {...props} />
    ) : t === "task" ? (
      <TaskView {...props} />
    ) : t === "workflow" ? (
      <WorkflowView {...props} />
    ) : (
      <MixedView {...props} />
    );
  const wide = t !== "scheduled" && t !== "task";
  return (
    <div
      role="group"
      aria-label={label}
      data-motion={motion}
      className={cn(
        "grid min-w-0 gap-3 p-3",
        !wide && diagram != null && "sm:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]",
        className
      )}
    >
      <div className="min-w-0">{body}</div>
      {diagram != null && (
        <div className={cn("min-w-0 overflow-hidden rounded-sm border", wide && "h-56")}>
          {diagram}
        </div>
      )}
    </div>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-muted-foreground text-2xs mb-1.5 font-mono tracking-wide uppercase">
      {children}
    </h3>
  );
}

// ---------------------------------------------------------------- scheduled

function ScheduledView({ snapshot, runs = [], onSelectNode }: JobsLayoutProps) {
  const byId = new Map(snapshot.nodes.map((n) => [n.id, n]));
  const schedules = snapshot.nodes.filter((n) => n.role === "schedule");
  const pairs = schedules.flatMap((s) =>
    snapshot.edges
      .filter((e) => e.from === s.id)
      .map((e) => byId.get(e.to))
      .filter((j): j is AppNode => !!j)
      .map((job) => ({ s, job }))
  );
  return (
    <div className="space-y-3">
      {pairs.map(({ s, job }) => {
        const sched = parseSchedule(s.name);
        const next = sched ? nextRun(sched, snapshot.now) : null;
        const slots = sched ? pastRuns(sched, snapshot.now, HISTORY) : [];
        const jobRuns = runs.filter((r) => r.jobId === job.id);
        const failing = job.health === "failing";
        return (
          <section
            key={`${s.id}-${job.id}`}
            {...selectProps(job.id, onSelectNode)}
            aria-label={`${job.name}: ${next ? `next run in ${formatCountdown(next - snapshot.now)}` : "schedule not parsed"}, ${healthLabel(job)}`}
            className={cn("bg-card space-y-3 rounded-sm border p-3", FOCUS_RING)}
            style={{
              borderColor: failing ? tint(HEALTH_COLOR.failing, 70) : undefined,
              boxShadow: glow(job),
            }}
          >
            <div className="flex min-w-0 items-center gap-2">
              <HealthDot health={job.health} />
              <span className="min-w-0 flex-1 truncate text-sm font-medium" title={job.name}>
                {job.name}
              </span>
              <span
                className="text-muted-foreground shrink-0 truncate font-mono text-xs"
                title={s.name}
              >
                {sched?.label ?? truncate(s.name, 24)}
              </span>
            </div>
            <div>
              <div className="text-muted-foreground text-xs">Next run in</div>
              <div className="font-mono text-2xl tabular-nums">
                {next ? formatCountdown(next - snapshot.now) : "unknown"}
              </div>
              {next && (
                <div className="text-muted-foreground font-mono text-xs">
                  {new Date(next).toISOString().slice(0, 16).replace("T", " ")} UTC
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 font-mono text-xs">
              <span className="text-muted-foreground w-20 shrink-0">
                {running(job) ? "running" : failing ? "failing" : "not running"}
              </span>
              <LoadBar node={job} />
            </div>
            {slots.length > 0 && (
              <div>
                <div className="text-muted-foreground mb-1 text-xs">Last {slots.length} slots</div>
                <div className="flex gap-1" aria-label="Run history">
                  {slots.map((slot, i) => {
                    const end = slots[i + 1] ?? next ?? Infinity;
                    const run = jobRuns.find((r) => r.at >= slot && r.at < end);
                    const color = run
                      ? run.ok
                        ? HEALTH_COLOR.ok
                        : HEALTH_COLOR.failing
                      : undefined;
                    return (
                      <span
                        key={slot}
                        title={`${new Date(slot).toISOString().slice(0, 16).replace("T", " ")} UTC: ${run ? (run.ok ? "succeeded" : "failed") : "no record"}`}
                        className="h-5 flex-1 rounded-sm border"
                        style={{
                          background: color,
                          borderColor: color ?? "var(--muted-foreground)",
                          opacity: color ? 1 : 0.5,
                        }}
                      />
                    );
                  })}
                </div>
              </div>
            )}
          </section>
        );
      })}
      {pairs.length === 0 && (
        <p className="text-muted-foreground text-sm">No schedules attached to a job.</p>
      )}
    </div>
  );
}

// --------------------------------------------------------------------- task

function TaskView({ snapshot, onSelectNode }: JobsLayoutProps) {
  const byId = new Map(snapshot.nodes.map((n) => [n.id, n]));
  const job =
    snapshot.nodes.find((n) => n.role === "worker") ??
    snapshot.nodes.find((n) => n.role !== "data" && n.role !== "external");
  if (!job) return <p className="text-muted-foreground text-sm">No task in this app.</p>;
  const failing = job.health === "failing";
  const state = failing ? "Failed" : running(job) ? "Running" : "Not running";
  const out = snapshot.edges.filter((e) => e.from === job.id);
  return (
    <section
      {...selectProps(job.id, onSelectNode)}
      aria-label={`${job.name} run: ${state}`}
      className={cn("bg-card space-y-3 rounded-sm border p-4", FOCUS_RING)}
      style={{
        borderColor: failing ? tint(HEALTH_COLOR.failing, 70) : undefined,
        boxShadow: glow(job),
      }}
    >
      <SectionTitle>Run</SectionTitle>
      <div className="flex min-w-0 items-center gap-2">
        <HealthDot health={job.health} className="size-3" />
        <span className="min-w-0 flex-1 truncate text-lg font-medium" title={job.name}>
          {job.name}
        </span>
        <span
          className={cn("shrink-0 font-mono text-sm", failing && MOTION_CLASS.flicker)}
          style={{ color: HEALTH_COLOR[job.health] }}
        >
          {state}
        </span>
      </div>
      <dl className="grid grid-cols-3 gap-2 font-mono text-xs">
        <div>
          <dt className="text-muted-foreground">kind</dt>
          <dd>{job.kind}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">pods</dt>
          <dd>{job.replicas ? `${job.replicas.ready}/${job.replicas.desired}` : "n/a"}</dd>
        </div>
        <div>
          <dt className="text-muted-foreground">load</dt>
          <dd>{Math.round(job.load * 100)}%</dd>
        </div>
      </dl>
      <LoadBar node={job} />
      {out.length > 0 && (
        <ul className="space-y-1 text-xs">
          {out.map((e) => {
            const target = byId.get(e.to);
            const bad = edgeFailing(e);
            return (
              <li key={e.to} className="flex min-w-0 items-center gap-2 font-mono">
                <span className="text-muted-foreground shrink-0">writes</span>
                <span className="min-w-0 flex-1 truncate" title={target?.name}>
                  {target?.name ?? e.to}
                </span>
                <span className="shrink-0 tabular-nums">{fmtRps(e.rps)}</span>
                {bad && (
                  <span style={{ color: HEALTH_COLOR.failing }}>
                    {(e.errorRate * 100).toFixed(0)}% err
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// ----------------------------------------------------------------- workflow

const STEP_GAP = 150;
const LINE_Y = 34;

function WorkflowView({ snapshot, motion, flowParticles = true, onSelectNode }: JobsLayoutProps) {
  const steps = layerNodes(
    snapshot.nodes.filter((n) => n.role !== "data" && n.role !== "external"),
    snapshot.edges
  ).flat();
  const W = Math.max(1, steps.length - 1) * STEP_GAP + 120;
  const xOf = (i: number) => 60 + i * STEP_GAP;
  const maxRps = Math.max(1, ...snapshot.edges.map((e) => e.rps));
  return (
    <section aria-label="Workflow steps">
      <SectionTitle>Steps</SectionTitle>
      <svg viewBox={`0 0 ${W} 80`} className="block h-auto max-h-40 w-full">
        {steps.slice(1).map((step, i) => {
          const prev = steps[i];
          const e = snapshot.edges.find((x) => x.from === prev.id && x.to === step.id);
          const failing = e ? edgeFailing(e) : false;
          const idle = !e || e.rps === 0 || prev.health === "idle";
          const color = failing ? HEALTH_COLOR.failing : idle ? HEALTH_COLOR.idle : HEALTH_COLOR.ok;
          const rate = e ? e.rps / maxRps : 0;
          const moving = motion === "full" && flowParticles && !idle;
          return (
            <g key={step.id}>
              <line
                x1={xOf(i)}
                y1={LINE_Y}
                x2={xOf(i + 1)}
                y2={LINE_Y}
                stroke={tint(color, 45)}
                strokeWidth={3 + rate * 4}
                strokeLinecap="round"
              />
              {moving && (
                <line
                  x1={xOf(i)}
                  y1={LINE_Y}
                  x2={xOf(i + 1)}
                  y2={LINE_Y}
                  stroke={color}
                  strokeWidth={2}
                  strokeLinecap="round"
                  className={MOTION_CLASS.flow}
                  style={{ "--viz-flow-duration": flowDuration(rate) } as React.CSSProperties}
                />
              )}
            </g>
          );
        })}
        {steps.map((step, i) => {
          const color = HEALTH_COLOR[step.health];
          const active = running(step);
          const failing = step.health === "failing";
          return (
            <g
              key={step.id}
              {...selectProps(step.id, onSelectNode)}
              aria-label={`Step ${i + 1}, ${step.name}: ${failing ? "failing" : active ? "running" : "idle"}`}
              className="group cursor-pointer outline-none"
            >
              <title>{step.name}</title>
              <circle
                cx={xOf(i)}
                cy={LINE_Y}
                r={15}
                fill="none"
                stroke="var(--ring)"
                strokeWidth={2}
                className="opacity-0 group-focus-visible:opacity-100"
              />
              {(active || failing) && (
                <circle cx={xOf(i)} cy={LINE_Y} r={13} fill={tint(color, 25)} />
              )}
              <circle
                cx={xOf(i)}
                cy={LINE_Y}
                r={8}
                fill="var(--card)"
                stroke={color}
                strokeWidth={3}
                className={failing ? MOTION_CLASS.flicker : undefined}
              />
              <text
                x={xOf(i)}
                y={LINE_Y + 30}
                fontSize="12"
                textAnchor="middle"
                fill="var(--foreground)"
              >
                {truncate(step.name, 18)}
              </text>
              <text
                x={xOf(i)}
                y={LINE_Y + 44}
                fontSize="10"
                textAnchor="middle"
                className="font-mono"
                fill={failing ? HEALTH_COLOR.failing : "var(--muted-foreground)"}
              >
                {failing ? "failing" : active ? `running ${Math.round(step.load * 100)}%` : "idle"}
              </text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}

// -------------------------------------------------------------------- mixed

function lastEventByNode(snapshot: AppSnapshot) {
  const m = new Map<string, { id: string; age: number }>();
  for (const e of snapshot.events) {
    if (e.kind !== "invoked" && e.kind !== "agent_action") continue;
    const age = snapshot.now - e.at;
    const cur = m.get(e.nodeId);
    if (!cur || age < cur.age) m.set(e.nodeId, { id: e.id, age });
  }
  return m;
}

function MixedView({ snapshot, motion, onSelectNode }: JobsLayoutProps) {
  const sections = roleSections(snapshot.nodes);
  const last = lastEventByNode(snapshot);
  return (
    <div className="grid min-w-0 grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-3">
      {sections.map((s) => (
        <section key={s.title} aria-label={s.title} className="min-w-0 rounded-sm border p-2">
          <SectionTitle>
            {s.title} · {s.nodes.length}
          </SectionTitle>
          <ul className="space-y-1">
            {s.nodes.map((n) => {
              const ev = last.get(n.id);
              const recent = ev && ev.age < EVENT_WINDOW_MS;
              return (
                <li key={n.id}>
                  <div
                    {...selectProps(n.id, onSelectNode)}
                    aria-label={`${n.name}, ${n.kind}, ${healthLabel(n)}, load ${Math.round(n.load * 100)}%`}
                    className={cn(
                      "relative grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-2 gap-y-1 rounded-sm px-1.5 py-1",
                      FOCUS_RING
                    )}
                    style={{ boxShadow: glow(n) }}
                  >
                    <Flash
                      eventId={recent ? ev.id : undefined}
                      age={ev?.age ?? Infinity}
                      motion={motion}
                      color={HEALTH_COLOR.ok}
                    />
                    <HealthDot health={n.health} />
                    <span className="truncate text-xs" title={n.name}>
                      {n.name}
                    </span>
                    <span className="text-muted-foreground font-mono text-xs tabular-nums">
                      {n.rps !== undefined ? fmtRps(n.rps) : n.kind}
                    </span>
                    <span className="col-span-3">
                      <LoadBar node={n} />
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
