import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { ModelConnectionRequestsClient } from "./ModelConnectionRequestsClient";
import { connectionStoryDecorator } from "./model-connection-story-provider";
const meta = {
  title: "Screens/Models/ModelConnectionRequestsClient",
  component: ModelConnectionRequestsClient,
  decorators: [connectionStoryDecorator],
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionRequestsClient>;
export default meta;
export const CurrentRequester: StoryObj<typeof meta> = {
  play: async ({ canvasElement }) => {
    await expect(await within(canvasElement).findByText("Qwen production")).toBeVisible();
  },
};
