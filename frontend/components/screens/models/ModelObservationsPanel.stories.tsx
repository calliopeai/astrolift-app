import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { expect, userEvent, within } from "storybook/test";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import { ModelObservationsPanel } from "./ModelObservationsPanel";
import { modelObservationsProps } from "./model-observations.fixtures";
const meta = {
  title: "Screens/Models/ModelObservationsPanel",
  component: ModelObservationsPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelObservationsPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Full: Story = {
  args: modelObservationsProps,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: "Open full cluster catalogue" })).toHaveAttribute(
      "href",
      "/models?clusterId=cluster-one"
    );
    await expect(canvas.queryByRole("searchbox")).not.toBeInTheDocument();
    await userEvent.click(canvas.getByRole("button", { name: "Refresh observations" }));
  },
};
export const Loading: Story = {
  args: {
    ...modelObservationsProps,
    metrics: { data: null, loading: true, stale: false, error: null },
    density: { data: null, loading: true, stale: false, error: null },
  },
};
export const Denied: Story = {
  args: {
    ...modelObservationsProps,
    metrics: { data: null, loading: false, stale: false, error: "Owner permission required" },
    density: { data: null, loading: false, stale: false, error: "Cluster observations denied" },
  },
};
export const RefreshFailed: Story = {
  args: {
    ...modelObservationsProps,
    metrics: {
      ...modelObservationsProps.metrics,
      stale: true,
      error: "Observation refresh failed",
    },
  },
};
export const NoSeries: Story = {
  args: {
    ...modelObservationsProps,
    metrics: {
      ...modelObservationsProps.metrics,
      data: { ...modelObservationsProps.metrics.data!, metrics: [] },
    },
  },
};
export const Unconfigured: Story = {
  args: {
    ...modelObservationsProps,
    metrics: {
      ...modelObservationsProps.metrics,
      data: {
        ...modelObservationsProps.metrics.data!,
        metrics: modelObservationsProps.metrics.data!.metrics.map((metric) => ({
          ...metric,
          state: "UNCONFIGURED",
          value: null,
          samples: [],
          observedAt: null,
        })),
      },
    },
  },
};
export const Truncated: Story = {
  args: {
    ...modelObservationsProps,
    density: {
      ...modelObservationsProps.density,
      data: { ...modelObservationsProps.density.data!, modelCount: 31, truncated: true },
    },
  },
};
export const Stale: Story = {
  args: {
    ...modelObservationsProps,
    metrics: {
      ...modelObservationsProps.metrics,
      data: {
        ...modelObservationsProps.metrics.data!,
        metrics: modelObservationsProps.metrics.data!.metrics.map((metric) => ({
          ...metric,
          state: "STALE",
        })),
      },
    },
  },
};
export const French: Story = {
  args: modelObservationsProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Japanese: Story = {
  args: modelObservationsProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: modelObservationsProps,
  render: (args) => (
    <div style={{ width: 768 }}>
      <ModelObservationsPanel {...args} />
    </div>
  ),
};
