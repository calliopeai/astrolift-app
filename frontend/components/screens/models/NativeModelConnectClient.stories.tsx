import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { NativeModelConnectScreen } from "./NativeModelConnectScreen";
import { nativeConnectProps } from "./NativeModelConnectScreen.stories";
const meta = {
  title: "Screens/Models/NativeModelConnectClient",
  component: NativeModelConnectScreen,
  parameters: { layout: "fullscreen" },
} satisfies Meta<typeof NativeModelConnectScreen>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: nativeConnectProps };
