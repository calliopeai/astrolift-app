import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { fn } from "storybook/test";

import { APP_ERROR, APP_ERROR_LONG } from "../ops/metrics-ops-shell.fixtures";

import { AppErrorScreen } from "./AppErrorScreen";

const meta: Meta<typeof AppErrorScreen> = {
  title: "Screens/Shell/AppErrorScreen",
  component: AppErrorScreen,
  parameters: { layout: "fullscreen" },
  args: { ...APP_ERROR, onReset: fn() },
};
export default meta;

type Story = StoryObj<typeof AppErrorScreen>;

/** A thrown error with a server digest. */
export const WithDigest: Story = {};

/** A client-side error: no digest. */
export const WithoutDigest: Story = { args: { digest: undefined } };

/** An error with no message falls back to the generic line. */
export const EmptyMessage: Story = { args: { message: "", digest: undefined } };

export const LongStrings: Story = { args: APP_ERROR_LONG };
