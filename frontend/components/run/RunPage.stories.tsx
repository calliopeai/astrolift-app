import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

import {
  FAILED_LINES,
  FAILED_STEPS,
  LONG_LINES,
  LONG_SHA,
  LONG_STEPS,
  LONG_URL,
  RUN_LINES,
  RUNNING_STEPS,
} from "./fixtures";
import { RunPage, type RunPageProps } from "./RunPage";

/** The run and log archetype (spec 44 §5.5), live and finished. */
const meta: Meta = { title: "Run/RunPage", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

const WORKFLOW_FUNCTIONS = [
  { label: "Agents", href: "#agents" },
  { label: "Workflows", href: "#workflows", active: true },
  { label: "Runs", href: "#runs" },
];

function Run(props: Partial<RunPageProps>) {
  return (
    <RunPage
      crumbs={[
        { label: "Workflows", switcher: WORKFLOW_FUNCTIONS },
        { label: "nightly-sync", href: "#nightly-sync" },
        { label: "run 7f3c…" },
      ]}
      title="run 7f3c…"
      status={<Badge variant="secondary">running</Badge>}
      durationMs={252_000}
      context="schedule"
      primaryAction={
        <Button size="sm" variant="outline">
          Stop
        </Button>
      }
      steps={RUNNING_STEPS}
      log={{ lines: RUN_LINES, onDownload: () => {} }}
      {...props}
    />
  );
}

export const Running: Story = { render: () => <Run /> };

/** Finished and failed: the reason first, Redeploy as the action, the log at its end. */
export const Failed: Story = {
  render: () => (
    <Run
      crumbs={[
        { label: "Apps", href: "#apps" },
        { label: "checkout", href: "#checkout" },
        { label: "deploy 1112015d" },
      ]}
      title="deploy 1112015d"
      status={<Badge variant="destructive">failed</Badge>}
      durationMs={93_000}
      context="production · us-west-2"
      primaryAction={<Button size="sm">Redeploy</Button>}
      steps={FAILED_STEPS}
      failure={{ title: "Deploy failed", reason: "Image pull denied: authentication required" }}
      log={{ lines: FAILED_LINES, onDownload: () => {} }}
    />
  ),
};

export const StepSelected: Story = {
  render: () => {
    function Pick() {
      const [id, setId] = React.useState<string | null>("transform");
      return <Run selectedStepId={id} onSelectStep={setId} />;
    }
    return <Pick />;
  },
};

export const Loading: Story = {
  render: () => (
    <Run steps={[]} stepsLoading durationMs={null} log={{ lines: [], loading: true }} />
  ),
};
export const Empty: Story = {
  render: () => (
    <Run steps={[]} log={{ lines: [], emptyHint: "Queued. Output appears when the run starts." }} />
  ),
};
export const ErrorState: Story = {
  render: () => (
    <Run
      steps={[]}
      stepsError="runs.get: upstream timed out after 30s"
      onRetrySteps={() => {}}
      log={{ lines: [], error: "log stream closed: 502", onRetry: () => {} }}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Run
      title={`run ${LONG_SHA}`}
      context={LONG_URL}
      steps={LONG_STEPS}
      failure={{ reason: LONG_URL }}
      log={{ lines: LONG_LINES, onDownload: () => {} }}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-6">
      <Run
        steps={[...FAILED_STEPS, ...LONG_STEPS]}
        log={{ lines: [...LONG_LINES, ...RUN_LINES] }}
      />
    </div>
  ),
};
