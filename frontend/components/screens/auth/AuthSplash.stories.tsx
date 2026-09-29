import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG_TEXT } from "../shell/root-bare-login.fixtures";

import { AuthSplash } from "./AuthSplash";

const meta: Meta<typeof AuthSplash> = {
  title: "Screens/Auth/AuthSplash",
  component: AuthSplash,
  args: { message: "Completing login…" },
};
export default meta;

type Story = StoryObj<typeof AuthSplash>;

/** The /auth/callback page while it stores the token. */
export const Loading: Story = {};

export const Warning: Story = {
  args: {
    message: "No identity provider configured",
    detail: "An operator must configure an IdP before users can log in.",
    tone: "warning",
  },
};

export const Destructive: Story = {
  args: {
    message: "Couldn't reach the API",
    detail: "TypeError: Failed to fetch",
    tone: "destructive",
  },
};

export const LongStrings: Story = {
  args: { message: "Couldn't reach the API", detail: LONG_TEXT, tone: "destructive" },
};
