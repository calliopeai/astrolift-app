import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ModelSubscriptionsPanel } from "./ModelSubscriptionsPanel";
import { subscriptionProps } from "./shared-model.fixtures";
const meta = {
  title: "Screens/Models/ModelSubscriptionsClient",
  component: ModelSubscriptionsPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelSubscriptionsPanel>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: subscriptionProps };
