import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { expect, userEvent, within } from "storybook/test";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";
import { SharedModelPromptPanel } from "./SharedModelPromptPanel";
import { sharedModelPromptProps } from "./shared-model-prompt.fixtures";
const meta = {
  title: "Screens/Models/SharedModelPromptPanel",
  component: SharedModelPromptPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SharedModelPromptPanel>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Ready: Story = {
  args: sharedModelPromptProps,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole("textbox", { name: "Prompt" }), "Tell me a short story.");
    await userEvent.click(canvas.getByRole("button", { name: "Run model test" }));
    await expect(canvas.getByText("A bounded model reply.")).toBeInTheDocument();
  },
};
export const Unconfirmed: Story = {
  args: {
    ...sharedModelPromptProps,
    readiness: {
      ...sharedModelPromptProps.readiness,
      data: {
        ...sharedModelPromptProps.readiness.data!,
        state: "UNKNOWN_HEARTBEAT",
        eligible: false,
      },
    },
  },
};
export const Unsupported: Story = {
  args: {
    ...sharedModelPromptProps,
    readiness: {
      ...sharedModelPromptProps.readiness,
      data: { ...sharedModelPromptProps.readiness.data!, state: "UNSUPPORTED", eligible: false },
    },
  },
};
export const Loading: Story = {
  args: { ...sharedModelPromptProps, readiness: { data: null, loading: true, error: null } },
};
export const OwnerDenied: Story = {
  args: {
    ...sharedModelPromptProps,
    readiness: { data: null, loading: false, error: "Owner permission required" },
  },
};
export const FailedRequest: Story = {
  args: {
    ...sharedModelPromptProps,
    onRun: async () => ({
      ok: false,
      message: "Model version changed",
      code: "VERSION_MISMATCH",
      currentVersion: 6,
      requestedVersion: 5,
    }),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByRole("textbox", { name: "Prompt" }), "Explain the result.");
    await userEvent.click(canvas.getByRole("button", { name: "Run model test" }));
    await expect(canvas.getByRole("alert")).toHaveTextContent("VERSION_MISMATCH");
  },
};
export const French: Story = {
  args: sharedModelPromptProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="fr" messages={fr} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Japanese: Story = {
  args: sharedModelPromptProps,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: sharedModelPromptProps,
  render: (args) => (
    <div style={{ width: 768 }}>
      <SharedModelPromptPanel {...args} />
    </div>
  ),
};
