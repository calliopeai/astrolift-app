import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { resolveMotion, type VizPrefs } from "@/lib/viz-prefs";

import { PLAIN, REDUCED, VISUALIZATIONS } from "./settings-visualizations.fixtures";
import { VisualizationsScreen } from "./VisualizationsScreen";

/**
 * Settings › Visualizations. The preference is read synchronously from
 * local storage, so the page has no loading, empty or error state; the real
 * states are the defaults, other choices, and reduced motion.
 */
const meta: Meta = { title: "Screens/Settings/Visualizations/VisualizationsScreen" };
export default meta;

type Story = StoryObj;

/** The defaults: orbit, transit, auto, particles on. */
export const Full: Story = { render: () => <VisualizationsScreen {...VISUALIZATIONS} /> };

/** The plain styles: tables and cards, no particles. */
export const Plain: Story = { render: () => <VisualizationsScreen {...PLAIN} /> };

/** Reduced motion chosen: every preview is a still frame. */
export const ReducedMotion: Story = { render: () => <VisualizationsScreen {...REDUCED} /> };

function Interactive() {
  const [value, setValue] = React.useState<VizPrefs>(VISUALIZATIONS.value);
  return (
    <VisualizationsScreen
      value={value}
      onChange={(patch) => setValue((v) => ({ ...v, ...patch }))}
      motion={resolveMotion(value.motion, false)}
    />
  );
}

/** Choices apply to the previews straight away. */
export const Playground: Story = { render: () => <Interactive /> };
