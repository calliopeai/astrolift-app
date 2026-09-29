import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DEPLOY_STRATEGY, LONG, STRATEGY_APP } from "./app-overview-cards-b.fixtures";
import { DeployStrategyCardView } from "./DeployStrategyCard";

const meta: Meta<typeof DeployStrategyCardView> = {
  title: "Screens/Apps/Overview/DeployStrategyCard",
  component: DeployStrategyCardView,
  args: DEPLOY_STRATEGY,
  decorators: [
    (Story) => (
      <div className="p-6">
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof DeployStrategyCardView>;

export const Full: Story = {};

/** The card has no loading state of its own; this is the sheet mid-save. */
export const Saving: Story = { args: { saving: true, defaultOpen: true } };

/**
 * No deploy branch set, previews off: the card falls back to the default
 * branch and manual mode. The closest thing this card has to empty.
 */
export const Defaults: Story = {
  args: {
    app: { ...STRATEGY_APP, triggerMode: "manual", deployBranch: "", previewEnabled: false },
  },
};

/** A rejected save (server error or stale version) keeps the sheet open. */
export const SaveRejected: Story = {
  args: { defaultOpen: true, onSave: async () => false },
};

/** Cron mode shows the cron expression field in the sheet. */
export const CronSheet: Story = {
  args: {
    defaultOpen: true,
    app: { ...STRATEGY_APP, triggerMode: "cron", cronExpression: "0 6 * * *" },
  },
};

export const LongStrings: Story = {
  args: { app: { ...STRATEGY_APP, deployBranch: `release/${LONG}`, defaultBranch: LONG } },
};
