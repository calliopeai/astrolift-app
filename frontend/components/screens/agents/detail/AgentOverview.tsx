"use client";

import { ActivityIcon, CpuIcon, HistoryIcon, Loader2Icon, PlayIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import Link from "next/link";
import * as React from "react";

import { ListSummary } from "@/components/list/ListSummary";
import { Panel, PanelGrid } from "@/components/panel/Panel";
import { StatusDot } from "@/components/StatusDot";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { FleetView } from "@/components/viz/FleetView";
import { formatRelativeAge } from "@/lib/format";

import { runModeLabel } from "./AgentFrame";
import { runDot, runDuration, titleCase } from "./agent-runs-list";
import { agentSectionHref, agentTabHref } from "./agent-tabs-model";
import type { AgentOverviewProps, AgentOverviewTask } from "./use-agent-overview";

const runHref = (t: AgentOverviewTask) => `/agents/runs/${encodeURIComponent(t.id)}`;

function RunLine({ task }: { task: AgentOverviewTask }) {
  const took = runDuration(task.startedAt, task.finishedAt);
  return (
    <span className="flex min-w-0 items-center gap-3">
      <StatusDot status={runDot(task.status)} />
      <span className="min-w-0 flex-1 truncate font-mono text-xs" title={task.id}>
        {task.id}
      </span>
      <span className="text-muted-foreground shrink-0 text-xs">{titleCase(task.status)}</span>
      <span className="text-muted-foreground w-12 shrink-0 text-right font-mono text-xs tabular-nums">
        {took ?? ""}
      </span>
      <span
        className="text-muted-foreground w-20 shrink-0 text-right font-mono text-xs"
        title={task.createdAt}
      >
        {formatRelativeAge(task.createdAt)}
      </span>
    </span>
  );
}

/**
 * The agent's Overview (spec 44 §5.2; Leo's page rules 1 to 3), about two
 * screens at 1440x900: what it is doing first (the latest run, with the
 * overseer input while it runs, beside what it runs on), then the recent
 * runs as a summary that links to Runs, and the agent in the fleet view.
 * When the last run failed, the frame puts its reason above every tab, so
 * the page does not repeat it. Run now is the frame's primary action. Pure.
 */
export function AgentOverviewView({
  agent,
  detail,
  detailLoading,
  detailError,
  onRetryDetail,
  runs,
  fleet,
  onSelectAgent,
  sendingInput,
  onSendInput,
}: AgentOverviewProps) {
  const latest = runs.rows[0] ?? null;
  const running = runs.rows.find((t) => t.status.toLowerCase() === "running") ?? null;
  const skills = detail?.skills ?? [];
  const tools = new Set(skills.flatMap((b) => b.toolDefs.map((t) => t.id))).size;
  const slug = agent.slug;

  return (
    <PanelGrid>
      <Panel
        title="Latest run"
        icon={<PlayIcon className="size-4" />}
        span={6}
        loading={runs.loading}
        error={runs.error}
        onRetry={runs.onRetry}
        empty={
          latest
            ? null
            : {
                icon: <PlayIcon className="size-5" />,
                title: "Not run yet",
                description: "Use Run now above. The run shows here with a live status.",
              }
        }
        actions={
          latest && (
            <Link
              href={runHref(latest)}
              className="text-primary text-xs font-medium hover:underline"
            >
              Open run
            </Link>
          )
        }
      >
        {latest && (
          <div className="flex min-w-0 flex-col gap-4">
            <dl className="grid min-w-0 grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <Fact label="Status">
                <span className="inline-flex min-w-0 items-center gap-1.5">
                  <StatusDot status={runDot(latest.status)} />
                  <span className="truncate" title={latest.status}>
                    {titleCase(latest.status)}
                  </span>
                </span>
              </Fact>
              <Fact label="Run" mono title={latest.id}>
                <span className="block truncate">{latest.id}</span>
              </Fact>
              <Fact label="Started" mono title={latest.startedAt ?? undefined}>
                {latest.startedAt ? formatRelativeAge(latest.startedAt) : "Not yet"}
              </Fact>
              <Fact label="Took" mono>
                {runDuration(latest.startedAt, latest.finishedAt) ??
                  (latest.status.toLowerCase() === "running" ? "Running" : "Not finished")}
              </Fact>
            </dl>
            {running && (
              <OverseerInput taskId={running.id} onSendInput={onSendInput} sending={sendingInput} />
            )}
          </div>
        )}
      </Panel>

      <Panel
        title="Runtime"
        icon={<CpuIcon className="size-4" />}
        span={6}
        loading={detailLoading}
        error={detailError}
        onRetry={onRetryDetail}
        actions={
          <Link
            href={agentTabHref(slug, "configuration")}
            className="text-primary text-xs font-medium hover:underline"
          >
            Configuration
          </Link>
        }
      >
        <dl className="grid min-w-0 grid-cols-2 gap-3 text-sm sm:grid-cols-3">
          <Fact label="Image" mono title={detail?.imageRef || undefined} wide>
            <span className="[overflow-wrap:anywhere]">{detail?.imageRef || "Not set"}</span>
          </Fact>
          <Fact label="Run mode">{runModeLabel(agent.runFamily, agent.runMode)}</Fact>
          <Fact label="Brief">{detail?.brief ? "Assembled" : "None yet"}</Fact>
          <Fact label="Skills" mono>
            <Link
              href={agentSectionHref(slug, "skills", "skills")}
              className="hover:text-primary hover:underline"
            >
              {skills.length}
            </Link>
          </Fact>
          <Fact label="Tools" mono>
            <Link
              href={agentSectionHref(slug, "skills", "tools")}
              className="hover:text-primary hover:underline"
            >
              {tools}
            </Link>
          </Fact>
        </dl>
      </Panel>

      <ListSummary<AgentOverviewTask>
        title="Recent runs"
        icon={<HistoryIcon className="size-4" />}
        span={6}
        count={runs.count}
        rows={runs.rows}
        keyOf={(t) => t.id}
        renderRow={(t) => <RunLine task={t} />}
        rowHref={runHref}
        viewAllHref={agentTabHref(slug, "runs")}
        loading={runs.loading}
        error={runs.error}
        onRetry={runs.onRetry}
      />

      <div className="col-span-12 min-w-0 xl:col-span-6">
        {fleet ? (
          <FleetView
            snapshot={fleet}
            selectedAgentId={agent.id}
            onSelectAgent={onSelectAgent}
            title="In the fleet"
            description={`${agent.name} among ${fleet.agents.length} agents, grouped by project`}
          />
        ) : (
          <Panel title="In the fleet" icon={<ActivityIcon className="size-4" />} loading />
        )}
      </div>
    </PanelGrid>
  );
}

function Fact({
  label,
  mono,
  title,
  wide,
  children,
}: {
  label: string;
  mono?: boolean;
  title?: string;
  wide?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className={wide ? "col-span-2 min-w-0 sm:col-span-3" : "min-w-0"}>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className={mono ? "min-w-0 font-mono text-xs" : "min-w-0"} title={title}>
        {children}
      </dd>
    </div>
  );
}

/** A follow-up to the running run, delivered at its next turn boundary. */
function OverseerInput({
  taskId,
  onSendInput,
  sending,
}: {
  taskId: string;
  onSendInput: AgentOverviewProps["onSendInput"];
  sending: boolean;
}) {
  const t = useTranslations("agentFrame.input");
  const [message, setMessage] = React.useState("");
  const [sent, setSent] = React.useState<string[]>([]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const value = message.trim();
    if (!value || sending) return;
    if (await onSendInput(taskId, value)) {
      setSent((prior) => [...prior, value]);
      setMessage("");
    }
  }

  return (
    <form onSubmit={submit} className="flex min-w-0 flex-col gap-2 border-t pt-4">
      <p className="text-xs font-medium">{t("title")}</p>
      {sent.length > 0 && (
        <ul className="flex max-h-24 min-w-0 flex-col gap-1 overflow-y-auto">
          {sent.map((entry, index) => (
            <li
              key={`${entry}-${index}`}
              className="bg-muted/40 rounded-sm px-2 py-1 text-xs [overflow-wrap:anywhere]"
            >
              {entry}
            </li>
          ))}
        </ul>
      )}
      <div className="flex min-w-0 flex-col gap-2 sm:flex-row sm:items-end">
        <Textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder={t("placeholder")}
          disabled={sending}
          rows={2}
          aria-label={t("ariaLabel")}
        />
        <Button type="submit" size="sm" disabled={!message.trim() || sending}>
          {sending ? <Loader2Icon className="size-4 animate-spin" /> : t("send")}
        </Button>
      </div>
      <p className="text-muted-foreground text-xs">{t("hint")}</p>
    </form>
  );
}
