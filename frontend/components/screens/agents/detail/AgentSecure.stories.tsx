import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AgentSecureScreen } from "./AgentSecure";
import { LONG } from "./agent-observe-secure.fixtures";

/**
 * The Secure tab is a static Zentinelle gate placeholder: it has no loading,
 * empty, or error state, only the agent's name.
 */
const meta: Meta<typeof AgentSecureScreen> = {
  title: "Screens/Agents/Detail/AgentSecure",
  component: AgentSecureScreen,
  args: { agentName: "research-agent" },
};
export default meta;

type Story = StoryObj<typeof AgentSecureScreen>;

export const Full: Story = {};

export const LongStrings: Story = { args: { agentName: LONG } };
