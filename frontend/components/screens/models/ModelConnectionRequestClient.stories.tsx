import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import { ModelConnectionRequestClient } from "./ModelConnectionRequestClient";
import { connectionStoryDecorator } from "./model-connection-story-provider";
const meta = {
  title: "Screens/Models/ModelConnectionRequestClient",
  component: ModelConnectionRequestClient,
  args: { id: "request-one", version: 2, review: false },
  decorators: [connectionStoryDecorator],
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionRequestClient>;
export default meta;
export const CurrentRequester: StoryObj<typeof meta> = {
  play: async ({ canvasElement }) => {
    await expect(await within(canvasElement).findByText("request-one")).toBeVisible();
  },
};
