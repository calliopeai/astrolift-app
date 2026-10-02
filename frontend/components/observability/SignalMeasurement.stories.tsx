import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SignalMeasurement } from "./SignalMeasurement";

const meta: Meta<typeof SignalMeasurement> = {
  title: "Patterns/Observability/SignalMeasurement",
  component: SignalMeasurement,
  args: {
    signal: {
      name: "SATURATION_MEMORY",
      unit: "ratio",
      rangeSeconds: 300,
      samples: [],
      promql: "",
      reason: "NO_DATA_YET",
      measurement: {
        effectiveScope: "WORKLOAD",
        source: "CADVISOR_KUBE_STATE_METRICS",
        identityBasis: "VERIFIED_RUNTIME",
        available: false,
        unavailableReason: "MISSING_LIMITS",
        target: {
          organizationId: "019eb737-0100-7000-8000-000000000001",
          appId: "019eb737-0100-7000-8000-000000000002",
          appSlug: "fixture",
          environmentId: "019eb737-0100-7000-8000-000000000003",
          environmentName: "production",
          clusterId: "019eb737-0100-7000-8000-000000000004",
          namespace: "fixture-production",
          workloadId: "019eb737-0100-7000-8000-000000000005",
          workloadSlug: "api",
        },
        containers: [
          {
            podName: "api-very-long-current-physical-pod-name",
            podUid: "019eb737-0100-7000-8000-000000000006",
            containerName: "api",
            containerId: "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
          },
        ],
        usageSamples: [],
        limitSamples: [],
      },
    },
  },
};
export default meta;
type Story = StoryObj<typeof meta>;
export const MissingLimits: Story = {};
export const Unreported: Story = { args: { signal: undefined } };
export const Width768: Story = {};
