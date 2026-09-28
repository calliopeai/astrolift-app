import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AuthSplash } from "./AuthSplash";
import { LoginShell } from "./LoginShell";

/** A frame only; its content is the auth step on screen. */
const meta: Meta<typeof LoginShell> = {
  title: "Screens/Auth/LoginShell",
  component: LoginShell,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof LoginShell>;

export const WithSplash: Story = {
  args: { children: <AuthSplash message="Completing login…" /> },
};
