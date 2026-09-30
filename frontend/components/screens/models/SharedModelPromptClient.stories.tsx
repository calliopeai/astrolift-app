import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SharedModelPromptPanel } from "./SharedModelPromptPanel";
import { sharedModelPromptProps } from "./shared-model-prompt.fixtures";
// Adapter is exercised through actual Apollo HTTP tests; its portable frame is the pure screen contract.
const meta = {
  title: "Screens/Models/SharedModelPromptClient",
  component: SharedModelPromptPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SharedModelPromptPanel>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: sharedModelPromptProps };
