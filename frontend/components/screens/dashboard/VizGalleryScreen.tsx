import { PageShell } from "@/components/PageShell";
import { Card, CardContent } from "@/components/ui/card";
import { Section } from "@/components/ui/section";
import { StatTile } from "@/components/ui/stat-tile";
import {
  MiniBar,
  PipelineDag,
  RadialGauge,
  Sparkline,
  SparklineCell,
  TrendStat,
  type PipelineDagStage,
} from "@/components/viz";

// Dev-only gallery for the #1053 (B1) viz primitives. Not linked in nav;
// reachable at /dev/viz for design review. Safe to remove once the primitives
// are wired across the app.

const RISING = [3, 4, 4, 6, 5, 8, 7, 9, 12, 11, 14, 18];
const FALLING = [18, 15, 16, 12, 13, 9, 10, 7, 6, 5, 4, 2];
const NOISY = [5, 9, 4, 11, 6, 8, 3, 12, 7, 10, 5, 9];
const FLAT = [6, 6, 6, 6, 6, 6];

// A representative build → test → deploy-per-env pipeline run for the DAG demo.
const DEMO_STAGES: PipelineDagStage[] = [
  {
    id: "build",
    name: "build",
    status: "success",
    startedAt: "2026-07-04T00:00:00Z",
    finishedAt: "2026-07-04T00:01:12Z",
  },
  {
    id: "test",
    name: "test",
    status: "success",
    needs: ["build"],
    startedAt: "2026-07-04T00:01:12Z",
    finishedAt: "2026-07-04T00:02:40Z",
  },
  {
    id: "lint",
    name: "lint",
    status: "success",
    needs: ["build"],
    startedAt: "2026-07-04T00:01:12Z",
    finishedAt: "2026-07-04T00:01:39Z",
  },
  {
    id: "deploy-staging",
    name: "deploy · staging",
    status: "success",
    needs: ["test", "lint"],
    startedAt: "2026-07-04T00:02:40Z",
    finishedAt: "2026-07-04T00:03:30Z",
  },
  { id: "approve", name: "approval gate", status: "pending", needs: ["deploy-staging"] },
  {
    id: "deploy-prod",
    name: "deploy · prod",
    status: "running",
    needs: ["approve"],
    startedAt: "2026-07-04T00:04:00Z",
  },
];

function Swatch({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-4">
        <p className="text-muted-foreground text-2xs font-medium tracking-wide uppercase">
          {title}
        </p>
        {children}
      </CardContent>
    </Card>
  );
}

export function VizGalleryScreen() {
  return (
    <PageShell
      title="Viz primitives"
      description="B1 (#1053) — small-viz gallery for design review."
    >
      <Section title="Sparkline" description="Axis-less micro trend for tiles and table cells.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Swatch title="line · chart-1">
            <Sparkline data={RISING} width={140} height={36} />
          </Swatch>
          <Swatch title="area · success">
            <Sparkline
              data={RISING}
              width={140}
              height={36}
              variant="area"
              className="text-success-fg"
            />
          </Swatch>
          <Swatch title="area · danger">
            <Sparkline
              data={FALLING}
              width={140}
              height={36}
              variant="area"
              className="text-danger-fg"
            />
          </Swatch>
          <Swatch title="flat / empty">
            <div className="flex flex-col gap-2">
              <Sparkline data={FLAT} width={140} height={36} />
              <Sparkline data={[]} width={140} height={36} className="text-muted-foreground" />
            </div>
          </Swatch>
        </div>
      </Section>

      <Section
        title="TrendStat"
        description="Value + signed delta + optional sparkline; status-coloured."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Swatch title="up = good">
            <TrendStat value="1,204" delta={12.4} deltaLabel="vs last week" data={RISING} />
          </Swatch>
          <Swatch title="down = bad">
            <TrendStat value="312" delta={-8.1} deltaLabel="vs last week" data={FALLING} />
          </Swatch>
          <Swatch title="invert (cost) up = bad">
            <TrendStat value="$4,180" delta={6.2} deltaLabel="vs prev month" invert data={RISING} />
          </Swatch>
          <Swatch title="flat / no delta">
            <TrendStat value="87" delta={0} deltaLabel="vs prev" />
          </Swatch>
        </div>
      </Section>

      <Section
        title="RadialGauge / ProgressRing"
        description="Ratios — success rate, quota, rollout."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Swatch title="92% · success">
            <RadialGauge value={0.92} className="text-success-fg" />
          </Swatch>
          <Swatch title="61% · warning">
            <RadialGauge value={0.61} className="text-warning-fg" />
          </Swatch>
          <Swatch title="18% · danger">
            <RadialGauge value={0.18} className="text-danger-fg" />
          </Swatch>
          <Swatch title="custom label">
            <RadialGauge value={0.75} label="3/4" className="text-primary" size={72} />
          </Swatch>
        </div>
      </Section>

      <Section title="MiniBar" description="Compact bar series — deploys/day, runs/hour.">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Swatch title="chart-1">
            <MiniBar data={NOISY} width={140} height={40} />
          </Swatch>
          <Swatch title="info">
            <MiniBar data={RISING} width={140} height={40} className="text-info-fg" />
          </Swatch>
          <Swatch title="with a zero gap">
            <MiniBar
              data={[4, 0, 6, 2, 0, 8, 3]}
              width={140}
              height={40}
              className="text-warning-fg"
            />
          </Swatch>
          <Swatch title="empty">
            <MiniBar data={[]} width={140} height={40} />
          </Swatch>
        </div>
      </Section>

      <Section
        title="StatTile composition"
        description="How the primitives slot into #A2 StatTile."
      >
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <StatTile
            label="Deploys (7d)"
            value={42}
            sparkline={<Sparkline data={RISING} className="text-success-fg" />}
            trend={<span className="text-success-fg text-xs">▲ 12% vs prev</span>}
          />
          <StatTile
            label="Error rate"
            value="0.4%"
            sparkline={<Sparkline data={FALLING} variant="area" className="text-success-fg" />}
          />
          <StatTile
            label="Cost MTD"
            value="$4,180"
            trend={<TrendStat value="" delta={6.2} deltaLabel="vs prev month" invert />}
          />
        </div>
      </Section>

      <Section title="SparklineCell" description="Table-cell renderer — line + trailing value.">
        <Card>
          <CardContent className="divide-border divide-y p-0">
            {[
              { name: "api-gateway", series: RISING },
              { name: "worker-pool", series: NOISY },
              { name: "cron-runner", series: FALLING },
            ].map((r) => (
              <div key={r.name} className="flex items-center justify-between px-4 py-2.5 text-sm">
                <span className="font-mono">{r.name}</span>
                <SparklineCell data={r.series} className="w-32" />
              </div>
            ))}
          </CardContent>
        </Card>
      </Section>

      <Section
        title="PipelineDag (B2 · #1054)"
        description="build → test → deploy-per-env + approval gate, on the shared FlowGraph."
      >
        <PipelineDag stages={DEMO_STAGES} height={340} />
      </Section>
    </PageShell>
  );
}
