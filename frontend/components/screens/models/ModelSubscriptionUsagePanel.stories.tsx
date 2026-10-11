import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ModelSubscriptionUsagePanel } from "./ModelSubscriptionUsagePanel";
import { observation } from "./model-observations.fixtures";
const data = {
  serviceId: "model-guid",
  clusterId: "cluster-guid",
  subscriptionId: "subscription-guid",
  start: "2026-10-03T12:00:00Z",
  end: "2026-10-03T12:15:00Z",
  retrievedAt: "2026-10-03T12:15:01Z",
  stepSeconds: 30,
  scope: "authenticated_subscription",
  metrics: [
    observation("requests_per_second", "requests/s", 2),
    observation("error_requests_per_second", "requests/s", 0),
    observation("response_bytes_per_second", "bytes/s", 500),
    observation("latency_p95", "seconds", 0.4),
  ].map((metric) => ({ ...metric, source: "authenticated_model_subscription" })),
};
const meta = {
  title: "Screens/Models/ModelSubscriptionUsagePanel",
  component: ModelSubscriptionUsagePanel,
  args: {
    appSlug: "storefront",
    environmentName: "production",
    read: { data, loading: false, stale: false, error: null },
    onRefresh: () => {},
    onClose: () => {},
  },
} satisfies Meta<typeof ModelSubscriptionUsagePanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Available: Story = {};
export const Loading: Story = {
  args: { read: { data: null, loading: true, stale: false, error: null } },
};
export const Unconfigured: Story = {
  args: {
    read: {
      data: {
        ...data,
        metrics: data.metrics.map((metric) => ({
          ...metric,
          state: "UNCONFIGURED",
          value: null,
          samples: [],
        })),
      },
      loading: false,
      stale: false,
      error: null,
    },
  },
};
export const NoData: Story = {
  args: {
    read: {
      data: {
        ...data,
        metrics: data.metrics.map((metric) => ({
          ...metric,
          state: "NO_DATA",
          value: null,
          samples: [],
        })),
      },
      loading: false,
      stale: false,
      error: null,
    },
  },
};
export const Stale: Story = {
  args: { read: { data, loading: false, stale: true, error: "Read refused" } },
};
