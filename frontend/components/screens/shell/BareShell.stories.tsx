import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { BareShell } from "./BareShell";

/** A frame only: it has no states of its own, so the stories vary its content. */
const meta: Meta<typeof BareShell> = {
  title: "Screens/Shell/BareShell",
  component: BareShell,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof BareShell>;

export const WithContent: Story = {
  args: {
    children: (
      <div className="text-muted-foreground flex h-full items-center justify-center text-sm">
        Whole-window surface
      </div>
    ),
  },
};

export const Empty: Story = { args: { children: null } };
