import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { ManagedServiceMetricsPanel } from "@/components/observability/ManagedServiceMetricsPanel";

import { METRICS, SERVICE_ROW } from "./app-managed-services.fixtures";
import { ServiceDetailSheet } from "./ServiceDetailSheet";

const meta: Meta = {
  title: "Screens/Apps/ManagedServices/ServiceDetailSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const noop = () => {};

export const Full: Story = {
  render: () => (
    <ServiceDetailSheet
      service={SERVICE_ROW}
      onOpenChange={noop}
      metrics={<ManagedServiceMetricsPanel {...METRICS} />}
    />
  ),
};

export const MetricsLoading: Story = {
  render: () => (
    <ServiceDetailSheet
      service={SERVICE_ROW}
      onOpenChange={noop}
      metrics={<ManagedServiceMetricsPanel {...METRICS} data={null} loading />}
    />
  ),
};

/** The resolver has no metrics for this kind: the panel renders nothing. */
export const NoMetrics: Story = {
  render: () => (
    <ServiceDetailSheet
      service={{ ...SERVICE_ROW, kind: "redis", variant: null }}
      onOpenChange={noop}
      metrics={<ManagedServiceMetricsPanel {...METRICS} data={null} />}
    />
  ),
};

export const LongStrings: Story = {
  render: () => (
    <ServiceDetailSheet
      service={{
        ...SERVICE_ROW,
        name: "checkout-primary-database-with-a-deliberately-long-service-name",
        variant: "serverless-v2-min-0.5-max-128-acu-with-a-long-variant-label",
        environmentName: "production-us-west-2-blue-green-candidate",
        status: "deprovisioning",
      }}
      onOpenChange={noop}
      metrics={<ManagedServiceMetricsPanel {...METRICS} />}
    />
  ),
};
