import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NextIntlClientProvider } from "next-intl";
import { expect, within } from "storybook/test";
import ja from "@/messages/ja.json";
import { InstallManagedModelCard } from "./InstallManagedModelCard";

const meta = {
  title: "Screens/Models/InstallManagedModelCard",
  component: InstallManagedModelCard,
} satisfies Meta<typeof InstallManagedModelCard>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Reported: Story = {
  args: { model: { modelId: "Qwen/Qwen2.5-7B-Instruct", replicas: 2 } },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByRole("region", { name: "Managed by the install" })).toBeVisible();
    await expect(canvas.getByText("read-only")).toBeVisible();
    await expect(canvas.queryByRole("link")).toBeNull();
    await expect(canvas.queryByRole("button")).toBeNull();
  },
};
export const ReplicasNotReported: Story = {
  args: { model: { modelId: "Qwen/Qwen2.5-7B-Instruct", replicas: null } },
};
export const Japanese: Story = {
  args: { model: { modelId: "Qwen/Qwen2.5-14B-Instruct", replicas: 1 } },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export const Width768: Story = {
  args: {
    model: {
      modelId: "organization/a-very-long-open-weight-model-name-that-does-not-fit-on-one-line-1",
      replicas: 4,
    },
  },
  render: (args) => (
    <div style={{ width: 768 }}>
      <InstallManagedModelCard {...args} />
    </div>
  ),
};
