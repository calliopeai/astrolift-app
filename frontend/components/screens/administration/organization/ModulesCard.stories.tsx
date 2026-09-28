import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { modules, modulesAllOff, modulesLoading, modulesLong, modulesReadOnly } from "./fixtures";
import { ModulesCard } from "./ModulesCard";

const meta: Meta<typeof ModulesCard> = {
  title: "Screens/Administration/Organization/ModulesCard",
  component: ModulesCard,
  args: modules,
};
export default meta;

type Story = StoryObj<typeof ModulesCard>;

export const Full: Story = {};

export const Loading: Story = { args: modulesLoading };

/** Every module off and forced off by the install. */
export const Empty: Story = { args: modulesAllOff };

/** A viewer without org.update: every switch is read-only. */
export const Error: Story = { args: modulesReadOnly };

export const Pending: Story = { args: { ...modules, pendingKey: "chat_studio_integration" } };

export const LongStrings: Story = { args: modulesLong };
