import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { EndpointMetricsPanel } from "@/components/observability/EndpointMetricsPanel";
import type { AstroliftAppEndpointMetric } from "@/graphql/__generated__/schema";

const meta: Meta = { title: "Patterns/Observability/EndpointMetricsPanel" };
export default meta;

const m = (
  route: string,
  requestRate: number,
  errorRateRatio: number,
  p50: number,
  p95: number,
  p99: number
) =>
  ({
    route,
    requestRate,
    errorRateRatio,
    p50Ms: p50,
    p95Ms: p95,
    p99Ms: p99,
    method: "GET",
  }) as unknown as AstroliftAppEndpointMetric;

export const Routes: StoryObj = {
  render: () => (
    <EndpointMetricsPanel
      loading={false}
      metrics={[
        m("/api/cart", 42.1, 0.002, 38, 120, 310),
        m("/api/checkout", 12.4, 0.031, 90, 480, 1200),
        m("/healthz", 1.0, 0, 2, 4, 6),
      ]}
    />
  ),
};
export const Loading: StoryObj = { render: () => <EndpointMetricsPanel loading metrics={[]} /> };
export const Empty: StoryObj = {
  render: () => <EndpointMetricsPanel loading={false} metrics={[]} />,
};
