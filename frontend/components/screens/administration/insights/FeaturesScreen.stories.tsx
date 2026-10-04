import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";
import { NextIntlClientProvider } from "next-intl";
import spanish from "@/messages/es.json";

import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

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

function Sectioned({ initial }: { initial: string | null }) {
  return <FeaturesScreen {...FEATURES} section={useLocalSettingsSection(initial)} />;
}

/** As the route mounts it: one list at a time, runtime flags first. */
export const RuntimeSection: Story = { render: () => <Sectioned initial={null} /> };

export const InstallTimeSection: Story = { render: () => <Sectioned initial="install-time" /> };

/** Expanded translated controls at the console minimum width. */
export const SpanishWidth768: Story = {
  render: () => (
    <NextIntlClientProvider locale="es" messages={spanish}>
      <div style={{ width: 768 }}>
        <FeaturesScreen {...FEATURES} />
      </div>
    </NextIntlClientProvider>
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("switch", { name: "Cambiar admin.cost_enabled" }));
    const dialog = await within(document.body).findByRole("alertdialog");
    await expect(within(dialog).getByRole("button", { name: "Activar función" })).toBeVisible();
  },
};

export const AcceptedRefreshFailed: Story = {
  render: () => (
    <FeaturesScreen
      {...FEATURES}
      controlsDisabled
      recovery={{ kind: "accepted", key: "admin.cost_enabled", enabled: true }}
      error={new globalThis.Error("Read unavailable")}
    />
  ),
};
export const UncertainWrite: Story = {
  render: () => (
    <FeaturesScreen
      {...FEATURES}
      controlsDisabled
      recovery={{ kind: "uncertain", key: "admin.cost_enabled", enabled: true }}
    />
  ),
};
