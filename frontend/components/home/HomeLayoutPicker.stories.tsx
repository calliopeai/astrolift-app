import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { BOTH, ONLY_APPS, OPERATOR } from "./fixtures";
import { HOME_QUESTION } from "./HomeScreen";
import { HomeLayoutPicker } from "./HomeLayoutPicker";
import {
  defaultLayoutFor,
  type HomeAccess,
  type HomeLayoutDef,
  type HomeLayoutKey,
  offeredLayouts,
} from "./registry";

/** The one Home question, as the first sign-in and Settings › Home ask it. */
const meta: Meta = { title: "Home/HomeLayoutPicker", parameters: { layout: "padded" } };
export default meta;

type Story = StoryObj;

function Live({
  access,
  layouts,
  showLegend = true,
  disabled,
}: {
  access: HomeAccess;
  layouts?: HomeLayoutDef[];
  showLegend?: boolean;
  disabled?: boolean;
}) {
  const [value, setValue] = React.useState<HomeLayoutKey | null>(defaultLayoutFor(access));
  return (
    <HomeLayoutPicker
      legend={HOME_QUESTION}
      showLegend={showLegend}
      layouts={layouts ?? offeredLayouts(access)}
      value={value}
      onChange={setValue}
      defaultLayout={defaultLayoutFor(access)}
      disabled={disabled}
    />
  );
}

export const Builder: Story = { render: () => <Live access={BOTH} /> };
export const Operator: Story = { render: () => <Live access={OPERATOR} /> };
export const OneLayout: Story = { render: () => <Live access={ONLY_APPS} /> };
export const HiddenLegend: Story = { render: () => <Live access={BOTH} showLegend={false} /> };
export const Disabled: Story = { render: () => <Live access={OPERATOR} disabled /> };

const LONG = "x".repeat(200);

export const LongStrings: Story = {
  render: () => (
    <Live
      access={BOTH}
      layouts={offeredLayouts(BOTH).map((l) => ({
        ...l,
        title: `${l.title} f1f9f11a0c2e4b7d9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f`,
        description: `${l.description} ${LONG}`,
      }))}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Live access={OPERATOR} />
    </div>
  ),
};
