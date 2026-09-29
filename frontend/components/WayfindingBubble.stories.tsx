import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { WayfindingBubble } from "@/components/WayfindingBubble";

/** Read-only help (#1101), opened from its bubble. */
const meta: Meta = { title: "Shell/WayfindingBubble", parameters: { layout: "fullscreen" } };
export default meta;

const open = async ({ canvasElement }: { canvasElement: HTMLElement }) => {
  await userEvent.click(
    within(canvasElement).getByRole("button", { name: "Open help and wayfinding" })
  );
  await expect(within(canvasElement).getByRole("dialog")).toBeTruthy();
};

export const Closed: StoryObj = {
  render: () => <WayfindingBubble turns={[]} pending={false} error={null} onAsk={() => {}} />,
};
export const Answered: StoryObj = {
  render: () => (
    <WayfindingBubble
      pending={false}
      error={null}
      onAsk={() => {}}
      turns={[
        {
          question: "Where do I set central auth for a cluster?",
          answer: "Open the cluster and go to Settings › Central auth.",
          routes: ["/clusters"],
        },
      ]}
    />
  ),
  play: open,
};
export const TurnedOff: StoryObj = {
  render: () => (
    <WayfindingBubble
      turns={[]}
      pending={false}
      error="Wayfinding is turned off for this install."
      onAsk={() => {}}
    />
  ),
  play: open,
};
