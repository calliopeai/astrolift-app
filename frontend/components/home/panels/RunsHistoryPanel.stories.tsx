import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import { FAILED, LOADING, QUIET_DAYS, READY, RUN_DAYS } from "./apps-agents.fixtures";
import { RunsHistoryPanelView } from "./RunsHistoryPanel";

/**
 * Seven days of agent runs and failures, with bounded totals.
 */
const meta: Meta<typeof RunsHistoryPanelView> = {
  title: "Home/Panels/RunsHistoryPanel",
  component: RunsHistoryPanelView,
  args: {
    panel: HOME_PANELS["runs-spend"],
    ...READY,
    days: RUN_DAYS,
    capped: false,
  },
  decorators: [
    (Story) => (
      <PanelGrid>
        <Story />
      </PanelGrid>
    ),
  ],
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj<typeof RunsHistoryPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("178")).toBeInTheDocument();
  },
};

/** The week holds more runs than the window counted: the totals are a floor. */
export const Capped: Story = {
  args: { capped: true },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("178+")).toBeInTheDocument();
  },
};

export const Quiet: Story = {
  args: { days: QUIET_DAYS },
};

export const Loading: Story = { args: LOADING };

export const QueryError: Story = { args: FAILED };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};

export const FrenchCappedWidth768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: { capped: true },
  globals: { viewport: { value: "width768" } },
};
