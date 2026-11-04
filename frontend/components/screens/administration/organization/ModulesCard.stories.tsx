import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  modules,
  modulesFreshInstall,
  modulesLicence,
  modulesLoading,
  modulesLong,
  modulesNone,
  modulesReadOnly,
} from "./fixtures";
import { ModulesCard } from "./ModulesCard";

const meta: Meta<typeof ModulesCard> = {
  title: "Screens/Administration/Organization/ModulesCard",
  component: ModulesCard,
  args: modules,
};
export default meta;

type Story = StoryObj<typeof ModulesCard>;

/** An install that offers the Chat Studio integration keeps its switch. */
export const Full: Story = {};

export const Loading: Story = { args: modulesLoading };

/** A new install does not offer the Chat Studio integration, so it is not listed (#2551). */
export const FreshInstall: Story = { args: modulesFreshInstall };

/** The install offers no module. */
export const Empty: Story = { args: modulesNone };

/** A viewer without org.update: every switch is read-only. */
export const ReadOnly: Story = { args: modulesReadOnly };
export const Error: Story = {
  args: { error: { name: "Error", message: "Permission denied while loading this section" } },
};

/** A module the licence turned off before granting it again. */
export const Licence: Story = { args: modulesLicence };

export const Pending: Story = { args: { ...modules, pendingKey: "chat_studio_integration" } };

export const LongStrings: Story = { args: modulesLong };

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <ModulesCard {...modulesLong} />
    </div>
  ),
};
