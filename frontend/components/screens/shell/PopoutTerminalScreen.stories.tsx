import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PopoutTerminalScreen } from "./PopoutTerminalScreen";
import { POPOUT_TARGET, POPOUT_TARGET_LONG } from "./root-bare-login.fixtures";

/**
 * The live terminal is a slot: in the app it is TerminalEmulator, which
 * opens an exec websocket, so the catalog stands in a static pane. Loading
 * and error belong to the terminal itself, not this screen.
 */
const terminal = (
  <div className="bg-muted text-muted-foreground min-h-0 flex-1 rounded-md p-3 font-mono text-xs">
    $ ls /app
  </div>
);

const meta: Meta<typeof PopoutTerminalScreen> = {
  title: "Screens/Shell/PopoutTerminalScreen",
  component: PopoutTerminalScreen,
  parameters: { layout: "fullscreen" },
  args: { ...POPOUT_TARGET, terminal },
  decorators: [
    (Story) => (
      <div className="h-96">
        <Story />
      </div>
    ),
  ],
};
export default meta;

type Story = StoryObj<typeof PopoutTerminalScreen>;

export const WithTarget: Story = {};

/** The URL names no pod or container. */
export const NoTarget: Story = { args: { podName: "", container: "" } };

export const LongStrings: Story = { args: POPOUT_TARGET_LONG };
