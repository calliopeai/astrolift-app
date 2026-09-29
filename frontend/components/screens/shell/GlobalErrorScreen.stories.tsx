import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fn } from "storybook/test";

import { GlobalErrorScreen } from "./GlobalErrorScreen";
import { GLOBAL_ERROR, GLOBAL_ERROR_LONG } from "./root-bare-login.fixtures";

/**
 * The hard-crash boundary. In the app it replaces the root layout (so no
 * design tokens); here it renders inside the catalog frame. No loading or
 * empty state exists.
 */
const meta: Meta<typeof GlobalErrorScreen> = {
  title: "Screens/Shell/GlobalErrorScreen",
  component: GlobalErrorScreen,
  parameters: { layout: "fullscreen" },
  args: { ...GLOBAL_ERROR, onReset: fn() },
};
export default meta;

type Story = StoryObj<typeof GlobalErrorScreen>;

export const WithDigest: Story = {};

export const WithoutDigest: Story = { args: { digest: undefined } };

/** No message falls back to the generic line. */
export const EmptyMessage: Story = { args: { message: "", digest: undefined } };

export const LongStrings: Story = { args: GLOBAL_ERROR_LONG };
