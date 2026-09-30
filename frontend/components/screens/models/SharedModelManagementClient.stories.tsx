import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SharedModelManagementPanel } from "./SharedModelManagementPanel";
import { sharedModelManagementProps } from "./shared-model-management.fixtures";
const meta = {
  title: "Screens/Models/SharedModelManagementClient",
  component: SharedModelManagementPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SharedModelManagementPanel>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: sharedModelManagementProps };
