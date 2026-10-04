import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ModelAddChoicePanel } from "./ModelAddChoicePanel";
const meta = {
  title: "Screens/Models/ModelAddChoiceClient",
  component: ModelAddChoicePanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelAddChoicePanel>;
export default meta;
export const Presentation: StoryObj<typeof meta> = {
  args: {
    hosting: { allowed: true, reason: null },
    connection: { allowed: false, reason: "Native connections are disabled by the operator." },
  },
};
