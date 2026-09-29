import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Card, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

import { SecurityScreen } from "./SecurityScreen";

/**
 * Settings › Security frame. The cards are slots with their own stories
 * (Screens/Settings/Security); stand-ins show the layout here. The frame
 * itself has no loading, empty or error state.
 */
const meta: Meta = {
  title: "Screens/Settings/Account/SecurityScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const card = (title: string, description: string) => (
  <Card>
    <CardHeader>
      <CardTitle className="text-base">{title}</CardTitle>
      <CardDescription>{description}</CardDescription>
    </CardHeader>
  </Card>
);

export const Full: Story = {
  render: () => (
    <SecurityScreen
      sessions={card("Active sessions", "Sessions card slot")}
      pairDevice={card("Pair a device", "Pair-device card slot")}
    />
  ),
};

/** Only the sessions slot filled. */
export const SessionsOnly: Story = {
  render: () => (
    <SecurityScreen sessions={card("Active sessions", "Sessions card slot")} pairDevice={null} />
  ),
};
