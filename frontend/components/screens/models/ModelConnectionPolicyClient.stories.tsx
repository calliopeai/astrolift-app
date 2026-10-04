import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import en from "@/messages/en.json";
import { ModelConnectionPolicyClient } from "./ModelConnectionPolicyClient";
import { connectionStoryDecorator } from "./model-connection-story-provider";
const meta = {
  title: "Screens/Models/ModelConnectionPolicyClient",
  component: ModelConnectionPolicyClient,
  decorators: [connectionStoryDecorator],
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelConnectionPolicyClient>;
export default meta;
export const CurrentPolicy: StoryObj<typeof meta> = {
  play: async ({ canvasElement }) => {
    await expect(
      await within(canvasElement).findByRole("button", {
        name: en.models.shared.connections.savePolicy,
      })
    ).toBeEnabled();
  },
};
