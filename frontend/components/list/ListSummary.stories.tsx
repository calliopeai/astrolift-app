import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { RocketIcon } from "lucide-react";
import type { ReactNode } from "react";

import { PanelGrid } from "@/components/panel/Panel";
import { LONG_ARN, LONG_SHA, LONG_URL } from "@/components/run/fixtures";

import { ListSummary, type ListSummaryProps } from "./ListSummary";

/**
 * A second list on an overview (list rule 3): a count, the top rows and
 * "View all" to the full list. Never a second table.
 */
const meta: Meta = { title: "List/ListSummary", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

interface Deploy {
  id: string;
  sha: string;
  env: string;
  reason: string;
  ago: string;
}

const DEPLOYS: Deploy[] = Array.from({ length: 8 }, (_, i) => ({
  id: `d${i}`,
  sha: `1112015d${i}`.slice(0, 8),
  env: ["production", "staging", "preview-412"][i % 3]!,
  reason: ["Image pull denied", "Readiness probe failed", "Quota exceeded"][i % 3]!,
  ago: `${(i + 1) * 7}m ago`,
}));

function DeployLine({ deploy }: { deploy: Deploy }) {
  return (
    <div className="flex min-w-0 items-baseline gap-3">
      <span className="shrink-0 font-mono text-xs">{deploy.sha}</span>
      <span className="text-muted-foreground min-w-0 flex-1 truncate">
        {deploy.env} · {deploy.reason}
      </span>
      <span className="text-muted-foreground shrink-0 font-mono text-xs">{deploy.ago}</span>
    </div>
  );
}

const BASE: ListSummaryProps<Deploy> = {
  title: "Failed deploys",
  icon: <RocketIcon className="size-4" />,
  rows: DEPLOYS,
  count: 23,
  keyOf: (d) => d.id,
  renderRow: (d) => <DeployLine deploy={d} />,
  rowHref: (d) => `/apps/checkout/deployments/${d.id}`,
  viewAllHref: "/apps/checkout/deployments?status=failed",
};

const Frame = ({ children }: { children: ReactNode }) => <div className="max-w-xl">{children}</div>;

/** Eight rows in, five shown: the summary never grows into a table. */
export const Full: Story = {
  render: () => (
    <Frame>
      <ListSummary {...BASE} />
    </Frame>
  ),
};

export const TopThree: Story = {
  render: () => (
    <Frame>
      <ListSummary {...BASE} limit={3} />
    </Frame>
  ),
};

/** No cheap count: "View all" without a number. */
export const NoCount: Story = {
  render: () => (
    <Frame>
      <ListSummary {...BASE} count={null} />
    </Frame>
  ),
};

export const Loading: Story = {
  render: () => (
    <Frame>
      <ListSummary {...BASE} rows={[]} loading />
    </Frame>
  ),
};

export const Empty: Story = {
  render: () => (
    <Frame>
      <ListSummary
        {...BASE}
        rows={[]}
        count={0}
        empty={{
          icon: <RocketIcon className="size-5" />,
          title: "No failed deploys",
          description: "Every deploy in the last 30 days went out.",
        }}
      />
    </Frame>
  ),
};

export const EmptyWithoutSpec: Story = {
  render: () => (
    <Frame>
      <ListSummary {...BASE} rows={[]} count={0} />
    </Frame>
  ),
};

export const Error: Story = {
  render: () => (
    <Frame>
      <ListSummary {...BASE} rows={[]} error="upstream timed out after 30s" onRetry={() => {}} />
    </Frame>
  ),
};

export const LongStrings: Story = {
  render: () => (
    <Frame>
      <ListSummary
        {...BASE}
        title={`Deploys of ${LONG_ARN}`}
        rows={[
          { ...DEPLOYS[0]!, sha: LONG_SHA, reason: LONG_URL },
          { ...DEPLOYS[1]!, env: LONG_ARN },
        ]}
        renderRow={(d) => (
          <p className="min-w-0 [overflow-wrap:anywhere]">
            <span className="font-mono text-xs">{d.sha}</span>{" "}
            <span className="text-muted-foreground">
              {d.env} · {d.reason}
            </span>
          </p>
        )}
        count={1_204}
      />
    </Frame>
  ),
};

/** On an overview grid at 768px, beside another summary. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <PanelGrid>
        <ListSummary {...BASE} span={6} />
        <ListSummary
          {...BASE}
          title="Waiting approval"
          count={2}
          rows={DEPLOYS.slice(0, 2)}
          span={6}
        />
      </PanelGrid>
    </div>
  ),
};
