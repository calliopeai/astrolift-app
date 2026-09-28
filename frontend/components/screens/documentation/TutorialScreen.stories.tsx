import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DOCS_E_TUTORIAL, DOCS_E_TUTORIAL_LONG } from "./docs-e.fixtures";
import { TutorialScreen } from "./TutorialScreen";

const meta: Meta<typeof TutorialScreen> = {
  title: "Screens/Documentation/TutorialScreen",
  component: TutorialScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof TutorialScreen>;

/** Static data: no loading or error state; an unknown slug is a route-level notFound(). */
export const Full: Story = { args: { tutorial: DOCS_E_TUTORIAL } };

export const NoSteps: Story = { args: { tutorial: { ...DOCS_E_TUTORIAL, steps: [] } } };

export const LongStrings: Story = { args: { tutorial: DOCS_E_TUTORIAL_LONG } };
