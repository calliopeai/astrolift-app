import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import {
  FAILED,
  LOADING,
  LONG_URL,
  QUIET_DAYS,
  READY,
  RUN_DAYS,
  SPEND,
} from "./apps-agents.fixtures";
import { RunsSpendPanelView } from "./RunsSpendPanel";

/**
 * Runs & spend: seven days of agent runs, failed runs and, for a viewer
 * who may read billing, the organization's spend, with totals in mono.
 */
const meta: Meta<typeof RunsSpendPanelView> = {
  title: "Home/Panels/RunsSpendPanel",
  component: RunsSpendPanelView,
  args: {
    panel: HOME_PANELS["runs-spend"],
    ...READY,
    days: RUN_DAYS,
    capped: false,
    spend: SPEND,
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

type Story = StoryObj<typeof RunsSpendPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("178")).toBeInTheDocument();
    await expect(canvas.getByText("$106")).toBeInTheDocument();
  },
};

/** No billing.read: runs only. */
export const WithoutBilling: Story = { args: { spend: null } };

/** The week holds more runs than the window counted: the totals are a floor. */
export const Capped: Story = {
  args: { capped: true },
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("178+")).toBeInTheDocument();
  },
};

export const Quiet: Story = {
  args: { days: QUIET_DAYS, spend: { ...SPEND, perDay: [0, 0, 0, 0, 0, 0, 0], totalCents: 0 } },
};

export const Loading: Story = { args: LOADING };

export const QueryError: Story = { args: FAILED };

export const LongStrings: Story = {
  args: { spend: null, spendError: `upstream ${LONG_URL} timed out` },
};

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
