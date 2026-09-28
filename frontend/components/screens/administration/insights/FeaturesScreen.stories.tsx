import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { FEATURES, FEATURES_LONG } from "./fixtures";
import { FeaturesScreen } from "./FeaturesScreen";

const meta: Meta = {
  title: "Screens/Administration/Insights/Features",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

export const Full: Story = { render: () => <FeaturesScreen {...FEATURES} /> };

export const Loading: Story = {
  render: () => <FeaturesScreen {...FEATURES} runtimeFlags={[]} buildTimeFeatures={[]} loading />,
};

export const Empty: Story = {
  render: () => <FeaturesScreen {...FEATURES} runtimeFlags={[]} buildTimeFeatures={[]} />,
};

/** One flag mid-toggle: its switch spins, the others lock. */
export const Toggling: Story = {
  render: () => <FeaturesScreen {...FEATURES} pendingKey="admin.cost_enabled" />,
};

/**
 * The toggle fails: ConfirmDialog stays open on the rejection and toasts it.
 * The screen has no other error state.
 */
export const Error: Story = {
  render: () => (
    <FeaturesScreen
      {...FEATURES}
      toggleFlag={async () => {
        throw new globalThis.Error("Constance backend is read-only on this install.");
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("switch", { name: "Toggle admin.cost_enabled" }));
    const dialog = await within(document.body).findByRole("alertdialog");
    await userEvent.click(within(dialog).getByRole("button", { name: "Enable flag" }));
    await expect(within(document.body).getByRole("alertdialog")).toBeInTheDocument();
  },
};

export const LongStrings: Story = { render: () => <FeaturesScreen {...FEATURES_LONG} /> };

/** The inventory query failed: the error sits in the page with a Retry. */
export const QueryError: Story = {
  render: () => (
    <FeaturesScreen
      {...FEATURES}
      runtimeFlags={[]}
      buildTimeFeatures={[]}
      error={new globalThis.Error("Network error: the Astrolift API did not answer.")}
    />
  ),
};

/** The narrowest the web console goes (spec 44 §6). */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <FeaturesScreen {...FEATURES_LONG} />
    </div>
  ),
};
