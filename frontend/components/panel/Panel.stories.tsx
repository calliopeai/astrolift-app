import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { RocketIcon, ServerIcon, ServerOffIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

import { DEPLOY_FAILURE, LONG_ARN, LONG_SHA, LONG_URL, QUERY_ERROR, WORKLOADS } from "./fixtures";
import { Panel, PanelGrid, SkeletonRows } from "./Panel";

/** The shared panel (spec 44 §7) on the 12-column grid (§6), in every state. */
const meta: Meta = { title: "Panel/Panel", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

const icon = <ServerIcon className="size-4" />;
const rows = (
  <ul className="text-sm">
    {WORKLOADS.map((n) => (
      <li key={n} className="border-b py-2 font-mono text-xs last:border-0">
        {n}
      </li>
    ))}
  </ul>
);

export const Full: Story = {
  render: () => (
    <PanelGrid>
      <Panel
        icon={icon}
        title="Workload health"
        description="Per-Deployment readiness."
        actions={
          <Button size="sm" variant="outline">
            Refresh
          </Button>
        }
      >
        {rows}
      </Panel>
      <Panel span={6} icon={icon} title="Half width">
        {rows}
      </Panel>
      <Panel span={6} icon={icon} title="Half width, beside it">
        {rows}
      </Panel>
    </PanelGrid>
  ),
};

/** 12 · 8+4 · 4+4+4 · 3+3+3+3: every span on one grid. */
export const Spans: Story = {
  render: () => (
    <PanelGrid>
      <Panel title="span 12">{rows}</Panel>
      <Panel span={8} title="span 8">
        {rows}
      </Panel>
      <Panel span={4} title="span 4">
        {rows}
      </Panel>
      {[1, 2, 3].map((i) => (
        <Panel key={`t${i}`} span={4} title={`span 4 · ${i}`}>
          {rows}
        </Panel>
      ))}
      {[1, 2, 3, 4].map((i) => (
        <Panel key={`q${i}`} span={3} title={`span 3 · ${i}`}>
          <p className="font-mono text-2xl">{i * 7}</p>
        </Panel>
      ))}
    </PanelGrid>
  ),
};

export const Loading: Story = {
  render: () => (
    <PanelGrid>
      <Panel icon={icon} title="Workload health" loading />
      <Panel span={6} title="Custom skeleton" loading skeleton={<SkeletonRows count={5} />} />
    </PanelGrid>
  ),
};

export const Empty: Story = {
  render: () => (
    <PanelGrid>
      <Panel
        icon={icon}
        title="Workload health"
        empty={{
          icon: <ServerOffIcon className="size-5" />,
          title: "No workloads yet",
          description: "Deploy an app to see its workloads here.",
          actionHref: "#deploy",
          actionLabel: "Deploy an app",
          learnMoreHref: "#docs",
        }}
      />
    </PanelGrid>
  ),
};

export const ErrorState: Story = {
  render: () => (
    <PanelGrid>
      <Panel icon={icon} title="Workload health" error={QUERY_ERROR} onRetry={() => {}} />
      <Panel span={6} title="Error object" error={{ message: QUERY_ERROR }} onRetry={() => {}} />
    </PanelGrid>
  ),
};

/** §5.2: the failure's reason is the first thing in the first panel. */
export const Failure: Story = {
  render: () => (
    <PanelGrid>
      <Panel
        span={6}
        icon={<RocketIcon className="size-4" />}
        title="Latest deploy"
        failure={{
          title: "Deploy failed",
          reason: DEPLOY_FAILURE,
          action: (
            <Button size="sm" variant="outline">
              Redeploy
            </Button>
          ),
        }}
      >
        <p className="font-mono text-xs">1112015d · 4m ago</p>
      </Panel>
      <Panel span={6} icon={icon} title="Health">
        {rows}
      </Panel>
    </PanelGrid>
  ),
};

/** A table that fills the panel edge to edge. */
export const Flush: Story = {
  render: () => (
    <PanelGrid>
      <Panel title="Deployments" flush>
        <ul className="divide-y text-sm">
          {WORKLOADS.map((n) => (
            <li key={n} className="px-4 py-2 font-mono text-xs">
              {n}
            </li>
          ))}
        </ul>
      </Panel>
    </PanelGrid>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <PanelGrid>
      <Panel
        span={6}
        icon={icon}
        title={`Workload ${LONG_SHA}`}
        description={LONG_ARN}
        failure={{ reason: LONG_URL }}
        error={LONG_URL}
        onRetry={() => {}}
      />
      <Panel span={6} icon={icon} title="Identifiers" actions={<span>{LONG_SHA.slice(0, 8)}</span>}>
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{LONG_SHA}</p>
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{LONG_ARN}</p>
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{LONG_URL}</p>
      </Panel>
    </PanelGrid>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <PanelGrid>
        <Panel icon={icon} title="Full row">
          {rows}
        </Panel>
        <Panel span={6} icon={icon} title="Half below xl stacks">
          {rows}
        </Panel>
        <Panel span={3} icon={icon} title="Quarter below lg stacks">
          {rows}
        </Panel>
      </PanelGrid>
    </div>
  ),
};
