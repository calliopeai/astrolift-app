import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import ja from "@/messages/ja.json";
import { AgentSecretReadError } from "./AgentSecretReadError";

const meta: Meta<typeof AgentSecretReadError> = {
  title: "Screens/Agents/List/AgentSecretReadError",
  component: AgentSecretReadError,
  args: {
    label: "Environment recipe secret values",
    message: "Provider refused status read: agents/actual-org/ACTUAL_KEY",
    onRetry: fn(),
  },
};
export default meta;
type Story = StoryObj<typeof AgentSecretReadError>;

export const Default: Story = {
  play: async ({ canvasElement, args }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("alert")).toHaveTextContent(args.message);
    await userEvent.click(canvas.getByRole("button", { name: "Retry" }));
    await expect(args.onRetry).toHaveBeenCalledOnce();
  },
};

export const JapaneseWidth768: Story = {
  globals: { viewport: { value: "width768" } },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja}>
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: { label: ja.agentSecrets.values.valuesTitle },
};
