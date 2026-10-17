import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { NewBrainScreen } from "./NewBrainScreen";

/** The wizard's frame; each step's body has its own stories. */
const meta: Meta<typeof NewBrainScreen> = {
  title: "Screens/Brains/New/NewBrainScreen",
  component: NewBrainScreen,
  parameters: { layout: "padded" },
  args: {
    step: 2,
    onStep: fn(),
    onBack: fn(),
    onContinue: fn(),
    onCancel: fn(),
    children: <p className="text-muted-foreground text-sm">The step&apos;s fields render here.</p>,
  },
};
export default meta;

type Story = StoryObj<typeof NewBrainScreen>;

/** A finished step is a link back; the current one is marked. */
export const Step2: Story = {
  play: async ({ args, canvasElement }) => {
    const canvas = within(canvasElement);
    const steps = within(canvas.getByRole("list", { name: "Steps" }));
    await expect(steps.getByText("Details").closest("li")).toHaveAttribute("aria-current", "step");
    await userEvent.click(steps.getByRole("button", { name: /Source/ }));
    await expect(args.onStep).toHaveBeenCalledWith(1);
    await expect(canvas.getByRole("link", { name: "Semantic layers" })).toHaveAttribute(
      "href",
      "/brains"
    );
  },
};

export const FirstStep: Story = { args: { step: 1, onBack: undefined } };

/** The last step creates; Continue says so. */
export const Review: Story = {
  args: { step: 4 },
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("button", { name: "Create semantic layer" })
    ).toBeEnabled();
  },
};

/** The request is in flight: nothing can be changed. */
export const Creating: Story = { args: { step: 4, busy: true } };

/** A field still needs fixing. */
export const Blocked: Story = { args: { continueDisabled: true } };

/** Without app.create there is no form to fill in. */
export const Denied: Story = {
  args: { denied: true },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("You cannot create semantic layers")).toBeInTheDocument();
    await expect(canvas.queryByRole("button", { name: "Continue" })).not.toBeInTheDocument();
  },
};
