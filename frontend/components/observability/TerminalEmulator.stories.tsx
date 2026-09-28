import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { TerminalEmulator } from "@/components/observability/TerminalEmulator";

/**
 * A device component: the exec WebSocket is the component, so it has no data
 * props to fake. In the catalog there is no exec relay, so these show the
 * real connect and reconnect chrome around the terminal.
 */
const meta: Meta<typeof TerminalEmulator> = {
  title: "Patterns/Observability/TerminalEmulator",
  component: TerminalEmulator,
  args: { appSlug: "checkout", podName: "checkout-web-7d9f8b6c4-x2k9p", container: "web" },
};
export default meta;

type Story = StoryObj<typeof TerminalEmulator>;

export const Inline: Story = {};

/** The popped-out window: no resize handle, expand toggle or pop-out button. */
export const Standalone: Story = { args: { standalone: true, className: "h-[24rem]" } };
