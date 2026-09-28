import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { DeployActivityStrip } from "../detail/DeployActivityStrip";
import { ACTIVITY as DEPLOY_ACTIVITY } from "../detail/app-detail-shell.fixtures";
import { ActivityTimelineView } from "./ActivityTimeline";
import { ACTIVITY, ACTIVITY_LONG } from "./app-overview-cards-b.fixtures";

const meta: Meta<typeof ActivityTimelineView> = {
  title: "Screens/Apps/Overview/ActivityTimeline",
  component: ActivityTimelineView,
  args: ACTIVITY,
  decorators: [
    (Story) => (
      <div className="p-6">
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof ActivityTimelineView>;

export const Full: Story = {};

export const Loading: Story = { args: { events: [], loading: true } };

/** Nothing has happened to this app yet. */
export const Empty: Story = { args: { events: [] } };

/** The events query failed: the error and Retry sit inside the panel. */
export const QueryError: Story = {
  args: { events: [], error: "Network error: upstream timed out", onRetry: () => {} },
};

/** On the overview the deploy heatmap leads the panel. */
export const WithDeployStrip: Story = {
  args: { strip: <DeployActivityStrip {...DEPLOY_ACTIVITY} /> },
};

/** A chip that matches nothing shows the filter in the empty line. */
export const NoMatches: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: "Tokens" }));
    await userEvent.type(canvas.getByLabelText("Search activity"), "nothing-here");
    await expect(canvas.getByText(/No events match/)).toBeInTheDocument();
  },
};

export const LongStrings: Story = { args: ACTIVITY_LONG };

export const At768: Story = {
  args: { ...ACTIVITY_LONG, strip: <DeployActivityStrip {...DEPLOY_ACTIVITY} /> },
  render: (args) => (
    <div style={{ width: 768 }}>
      <ActivityTimelineView {...args} />
    </div>
  ),
};
