import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import * as React from "react";

import {
  PromqlQueryPanel,
  type PromqlQueryPanelProps,
} from "@/components/observability/PromqlQueryPanel";
import type { AstroliftExecutePromqlResult } from "@/graphql/__generated__/schema";

const meta: Meta = { title: "Patterns/Observability/PromqlQueryPanel" };
export default meta;

type Story = StoryObj;

const NOW = 1_790_000_000;

const series = (route: string, base: number) => ({
  metricLabels: { __name__: "http_requests_total", route },
  values: Array.from({ length: 12 }, (_, i) => ({
    ts: NOW - (11 - i) * 60,
    value: base + Math.sin(i / 2) * base * 0.3,
  })),
});

const RESULT = {
  ok: true,
  error: null,
  series: [series("/checkout", 42), series("/cart", 18)],
} as unknown as AstroliftExecutePromqlResult;

const DISCOVERY = {
  ok: true,
  error: null,
  names: [
    "checkout_orders_total",
    "checkout_latency_seconds",
    "cart_items",
    "payment_retries_total",
  ],
  truncated: false,
  limit: 200,
};

const base: PromqlQueryPanelProps = {
  open: false,
  onOpenChange: () => {},
  discovery: null,
  discoveryLoading: false,
  running: false,
  transportError: null,
  result: null,
  onRun: () => {},
};

/** The panel keeps its own open state here, the way usePromql does in the app. */
function Controlled(props: Partial<PromqlQueryPanelProps>) {
  const [open, setOpen] = React.useState(props.open ?? false);
  return <PromqlQueryPanel {...base} {...props} open={open} onOpenChange={setOpen} />;
}

export const Collapsed: Story = { render: () => <Controlled /> };

export const DiscoveryLoading: Story = {
  render: () => <Controlled open discoveryLoading />,
};

export const WithMetricNames: Story = {
  render: () => <Controlled open discovery={DISCOVERY} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(await c.findByText("checkout_orders_total"));
    await expect(c.getByLabelText("PromQL query")).toHaveValue(
      "sum(rate(checkout_orders_total[5m]))"
    );
  },
};

export const Running: Story = {
  render: () => <Controlled open discovery={DISCOVERY} running />,
};

export const Result: Story = {
  render: () => <Controlled open discovery={DISCOVERY} result={RESULT} />,
};

export const QueryError: Story = {
  render: () => (
    <Controlled
      open
      discovery={DISCOVERY}
      result={
        {
          ok: false,
          error: 'parse error at char 5: unexpected "(" in aggregation',
          series: [],
        } as unknown as AstroliftExecutePromqlResult
      }
    />
  ),
};

export const TransportError: Story = {
  render: () => <Controlled open transportError="Network error: Failed to fetch" />,
};

export const DiscoveryUnavailable: Story = {
  render: () => (
    <Controlled
      open
      discovery={{
        ok: false,
        error: "Prometheus is not reachable",
        names: [],
        truncated: false,
        limit: 200,
      }}
    />
  ),
};
