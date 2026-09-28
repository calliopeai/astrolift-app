import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ServerIcon, ServerOffIcon } from "lucide-react";

import { QUERY_FAILED } from "./fixtures";
import { StatusPanel, StatusPanelGrid } from "./StatusPanel";

const meta: Meta = {
  title: "Screens/Clusters/Status/StatusPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const icon = <ServerIcon className="size-4" />;
const rows = (
  <ul className="text-sm">
    {["checkout-web", "billing-api", "report-runner"].map((n) => (
      <li key={n} className="border-b py-2 font-mono text-xs last:border-0">
        {n}
      </li>
    ))}
  </ul>
);

export const Full: Story = {
  render: () => (
    <StatusPanelGrid>
      <StatusPanel icon={icon} title="Workload health" description="Per-Deployment readiness.">
        {rows}
      </StatusPanel>
      <StatusPanel span="half" icon={icon} title="Half width">
        {rows}
      </StatusPanel>
      <StatusPanel span="half" icon={icon} title="Half width, beside it">
        {rows}
      </StatusPanel>
    </StatusPanelGrid>
  ),
};

export const Loading: Story = {
  render: () => (
    <StatusPanelGrid>
      <StatusPanel icon={icon} title="Workload health" loading />
    </StatusPanelGrid>
  ),
};

export const Empty: Story = {
  render: () => (
    <StatusPanelGrid>
      <StatusPanel
        icon={icon}
        title="Workload health"
        empty={{
          icon: <ServerOffIcon className="size-5" />,
          title: "No deployment data",
          description: "Apiserver unreachable or no workloads running yet.",
        }}
      />
    </StatusPanelGrid>
  ),
};

export const ErrorState: Story = {
  render: () => (
    <StatusPanelGrid>
      <StatusPanel
        icon={icon}
        title="Workload health"
        error={QUERY_FAILED.error}
        onRetry={QUERY_FAILED.refetch}
      />
    </StatusPanelGrid>
  ),
};

const SHA = "f1f9f11a0c2e4b7d9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f";
const ARN =
  "arn:aws:eks:us-east-1:718519534729:cluster/prod-east-shared-multi-tenant-platform-cluster-for-regulated-workloads/nodegroup/general-purpose-arm64-graviton3-on-demand-with-a-very-long-name/0000";
const LONG_URL =
  "https://prometheus.internal.prod-east.example.com/api/v1/query_range?query=sum(rate(container_cpu_usage_seconds_total[5m]))";

export const LongStrings: Story = {
  render: () => (
    <StatusPanelGrid>
      <StatusPanel
        span="half"
        icon={icon}
        title={`Workload ${SHA}`}
        description={ARN}
        error={LONG_URL}
        onRetry={() => {}}
      />
      <StatusPanel span="half" icon={icon} title="Identifiers">
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{SHA}</p>
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{ARN}</p>
        <p className="font-mono text-xs [overflow-wrap:anywhere]">{LONG_URL}</p>
      </StatusPanel>
    </StatusPanelGrid>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <StatusPanelGrid>
        <StatusPanel icon={icon} title="Full row">
          {rows}
        </StatusPanel>
        <StatusPanel span="half" icon={icon} title="Half below xl stacks">
          {rows}
        </StatusPanel>
        <StatusPanel span="half" icon={icon} title="And this one too">
          {rows}
        </StatusPanel>
      </StatusPanelGrid>
    </div>
  ),
};
