import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NativeModelSettingsPanel } from "./NativeModelSettingsPanel";
import { nativeSettingsProps } from "./NativeModelSettingsPanel.stories";
const meta = {
  title: "Screens/Models/NativeModelSettingsClient",
  component: NativeModelSettingsPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof NativeModelSettingsPanel>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: nativeSettingsProps };
