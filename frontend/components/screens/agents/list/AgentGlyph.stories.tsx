import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AGENT_STATUS_ORDER } from "./agents-list";
import { AgentGlyph } from "./AgentGlyph";

/**
 * The agent's fleet mark. It has no data of its own, so there is no loading,
 * empty or error state; every status is shown instead.
 */
const meta: Meta<typeof AgentGlyph> = {
  title: "Screens/Agents/List/AgentGlyph",
  component: AgentGlyph,
};
export default meta;

type Story = StoryObj<typeof AgentGlyph>;

export const Full: Story = {
  render: () => (
    <div className="flex flex-wrap items-center gap-4">
      {AGENT_STATUS_ORDER.map((status) => (
        <AgentGlyph key={status} status={status} />
      ))}
    </div>
  ),
};

/** Several runs in flight: the count sits on the glyph. */
export const ManyRunning: Story = { args: { status: "running", running: 12 } };

export const Idle: Story = { args: { status: "idle" } };

/** At the table's smaller size, beside a long name in a narrow column. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="flex min-w-0 items-center gap-2">
      <AgentGlyph status="failing" className="size-7" />
      <span className="min-w-0 truncate">{"a".repeat(64)}</span>
    </div>
  ),
};
