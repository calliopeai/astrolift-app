import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DOCS_E_TUTORIAL_LONG, DOCS_E_TUTORIALS } from "./docs-e.fixtures";
import { TutorialsScreen } from "./TutorialsScreen";

const meta: Meta<typeof TutorialsScreen> = {
  title: "Screens/Documentation/TutorialsScreen",
  component: TutorialsScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof TutorialsScreen>;

/** Static data: there is no loading or error state. */
export const Full: Story = { args: { tutorials: DOCS_E_TUTORIALS } };

export const Empty: Story = { args: { tutorials: [] } };

export const LongStrings: Story = { args: { tutorials: [DOCS_E_TUTORIAL_LONG] } };
