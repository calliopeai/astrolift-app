import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fn } from "storybook/test";

import { ROOT_ERROR, ROOT_ERROR_LONG } from "./root-bare-login.fixtures";
import { RootErrorScreen } from "./RootErrorScreen";

/** An error boundary has no loading or empty state; these are its real variants. */
const meta: Meta<typeof RootErrorScreen> = {
  title: "Screens/Shell/RootErrorScreen",
  component: RootErrorScreen,
  parameters: { layout: "fullscreen" },
  args: { ...ROOT_ERROR, onReset: fn() },
};
export default meta;

type Story = StoryObj<typeof RootErrorScreen>;

/** A thrown error with a server digest. */
export const WithDigest: Story = {};

/** A client-side error: no digest. */
export const WithoutDigest: Story = { args: { digest: undefined } };

/** An error with no message falls back to the generic line. */
export const EmptyMessage: Story = { args: { message: "", digest: undefined } };

export const LongStrings: Story = { args: ROOT_ERROR_LONG };
