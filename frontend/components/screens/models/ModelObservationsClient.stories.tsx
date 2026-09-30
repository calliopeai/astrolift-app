import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ModelObservationsPanel } from "./ModelObservationsPanel";
import { modelObservationsProps } from "./model-observations.fixtures";
// Adapter performs scoped network reads; portable stories exercise its pure presentation contract.
const meta = {
  title: "Screens/Models/ModelObservationsClient",
  component: ModelObservationsPanel,
  parameters: { layout: "padded" },
} satisfies Meta<typeof ModelObservationsPanel>;
export default meta;
export const Presentation: StoryObj<typeof meta> = { args: modelObservationsProps };
