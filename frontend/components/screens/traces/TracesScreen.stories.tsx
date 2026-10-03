import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ExplorerStory } from "../telemetry/TelemetryExplorerScreen.stories";
const meta: Meta = { title: "Screens/Traces/TracesScreen", parameters: { layout: "fullscreen" } };
export default meta;
export const Default: StoryObj = { render: () => <ExplorerStory mode="traces" /> };
