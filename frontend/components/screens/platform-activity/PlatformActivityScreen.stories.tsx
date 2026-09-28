import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { TEMPORAL_UI_URL } from "@/components/screens/events/events-downloads.fixtures";

import { PlatformActivityScreen } from "./PlatformActivityScreen";

const meta: Meta = {
  title: "Screens/PlatformActivity/PlatformActivityScreen",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/**
 * A static page: no loading, empty, error or long-string states exist.
 * The only variable is whether a Temporal UI link is configured.
 */
export const Full: Story = {
  render: () => <PlatformActivityScreen temporalUiUrl={TEMPORAL_UI_URL} />,
};

export const WithoutTemporalUi: Story = { render: () => <PlatformActivityScreen /> };
