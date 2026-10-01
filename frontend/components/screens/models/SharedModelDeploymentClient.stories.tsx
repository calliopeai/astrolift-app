import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { SharedModelDeploymentScreen } from "./SharedModelDeploymentScreen";
import { sharedDeploymentProps } from "./shared-model.fixtures";
// Real adapter is covered by Apollo boundary tests; the portable frame uses the same pure contract.
const meta = {
  title: "Screens/Models/SharedModelDeploymentClient",
  component: SharedModelDeploymentScreen,
  parameters: { layout: "padded" },
} satisfies Meta<typeof SharedModelDeploymentScreen>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: sharedDeploymentProps };
