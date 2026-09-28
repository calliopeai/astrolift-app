import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

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

/**
 * The card has no error state: a failed load leaves the feed empty, so this
 * is also what an error looks like.
 */
export const Empty: Story = { args: { events: [] } };

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
