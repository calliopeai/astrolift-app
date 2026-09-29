import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { GenerateSshKeySheet } from "./GenerateSshKeySheet";
import { GENERATE_KEY, KEYS, LONG_KEYS } from "./settings-source-providers.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/GenerateSshKeySheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = (patch: Partial<React.ComponentProps<typeof GenerateSshKeySheet>> = {}) => (
  <GenerateSshKeySheet {...GENERATE_KEY} open onOpenChange={() => {}} {...patch} />
);

/** The name / app slug form. The sheet has no loading or empty data state. */
export const Open: Story = { render: () => sheet() };

export const Generating: Story = { render: () => sheet({ generating: true }) };

/** After a successful generate: the public key to paste into the repo. */
export const Generated: Story = { render: () => sheet({ initialKey: KEYS[0] }) };

export const LongStrings: Story = { render: () => sheet({ initialKey: LONG_KEYS[0] }) };
