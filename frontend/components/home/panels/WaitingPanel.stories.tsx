import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { PanelGrid } from "@/components/panel/Panel";

import { HOME_PANELS } from "../registry";
import { WAITING, WAITING_LONG, FAILED, LOADING, READY } from "./apps-agents.fixtures";
import { WaitingPanelView } from "./WaitingPanel";

/** Waiting on you: deploy gates approved in place (behind a confirm), workflow gates and secret requests opened where they are decided. */
const meta: Meta<typeof WaitingPanelView> = {
  title: "Home/Panels/WaitingPanel",
  component: WaitingPanelView,
  args: {
    panel: HOME_PANELS["waiting"],
    ...READY,
    items: WAITING,
    approving: false,
    onApprove: fn(),
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

type Story = StoryObj<typeof WaitingPanelView>;

export const Full: Story = {
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("link", { name: /View all/ })).toHaveAttribute(
      "href",
      HOME_PANELS["waiting"].href
    );
  },
};

export const Loading: Story = { args: LOADING };

export const Empty: Story = { args: { items: [] } };

export const QueryError: Story = { args: { ...FAILED, items: [] } };

export const LongStrings: Story = { args: { items: WAITING_LONG } };

export const Width768: Story = {
  decorators: [
    (Story) => (
      <div style={{ width: 768 }}>
        <Story />
      </div>
    ),
  ],
};

/** Approve asks first; the confirm names the deploy. */
export const ApproveAsksFirst: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    // Gates and secret requests are decided where they live.
    await expect(canvas.getAllByRole("link", { name: "Review" })).toHaveLength(2);
    await userEvent.click(canvas.getByRole("button", { name: /Approve/ }));
    const dialog = within(document.body).getByRole("alertdialog");
    await expect(within(dialog).getByText(/checkout-web to production/)).toBeInTheDocument();
    await expect(args.onApprove).not.toHaveBeenCalled();
  },
};
