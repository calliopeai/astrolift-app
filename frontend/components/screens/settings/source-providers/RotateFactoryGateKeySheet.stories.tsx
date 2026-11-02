import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { RotateFactoryGateKeySheet } from "./RotateFactoryGateKeySheet";
import { ROTATE_FACTORY_GATE_KEY } from "./settings-source-providers.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/RotateFactoryGateKeySheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = (patch: Partial<React.ComponentProps<typeof RotateFactoryGateKeySheet>> = {}) => (
  <RotateFactoryGateKeySheet
    {...ROTATE_FACTORY_GATE_KEY}
    open
    onOpenChange={() => {}}
    owner="acme-corp"
    {...patch}
  />
);

/** Open, empty: paste the key generated on GitHub. */
export const Open: Story = { render: () => sheet() };

/** The mutation is in flight. The sheet has no loading or empty data state. */
export const Rotating: Story = { render: () => sheet({ rotating: true }) };

/** No owner on record: the GitHub link is left out. */
export const NoOwner: Story = { render: () => sheet({ owner: null }) };

export const LongStrings: Story = {
  render: () => sheet({ owner: "acme-corporation-platform-engineering-x" }),
};
