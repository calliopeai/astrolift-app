import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, SCALE } from "./app-workloads.fixtures";
import { ScalePopoverView } from "./ScalePopover";

const meta: Meta<typeof ScalePopoverView> = {
  title: "Screens/Apps/Workloads/ScalePopover",
  component: ScalePopoverView,
  args: SCALE,
};
export default meta;

type Story = StoryObj<typeof ScalePopoverView>;

/** The closed trigger, as it sits in the Ready / desired cell. */
export const Closed: Story = {};

export const Open: Story = { args: { defaultOpen: true } };

/** Apply in flight. */
export const Applying: Story = { args: { defaultOpen: true, loading: true } };

/** Scaled to zero: the closest this control has to an empty state. */
export const ScaledToZero: Story = { args: { defaultOpen: true, currentDesired: 0 } };

/** A rejected scale keeps the popover open (the hook toasts the reason). */
export const Rejected: Story = {
  args: { defaultOpen: true, apply: async () => false },
};

export const LongStrings: Story = { args: { defaultOpen: true, workloadName: LONG } };
