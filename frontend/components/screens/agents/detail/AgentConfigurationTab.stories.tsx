import { NextIntlClientProvider } from "next-intl";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

import { EDITOR as CONFIG_EDITOR } from "../../apps/config/app-config-manifest.fixtures";
import { PREVIEW_PROPS } from "../../apps/config/app-config-agent.fixtures";
import { ConfigEditorScreen } from "../../apps/config/ConfigEditorScreen";
import { ManifestPreviewScreen } from "../../apps/config/ManifestPreviewScreen";
import { AgentBuildScreen } from "./AgentBuildScreen";
import { BUILD, BUILD_ERROR, BUILD_LOADING, BUILD_LONG } from "./agent-build-run.fixtures";
import { CONTROL } from "./agent-control.fixtures";
import { MODEL_ACCESS } from "./agent-detail-shell.fixtures";
import { EDITOR as TRIGGER_EDITOR } from "./agent-trigger-editor.fixtures";
import { AgentConfigurationTab, type AgentConfigurationSlots } from "./AgentConfigurationTab";
import { AgentControlScreen } from "./AgentControl";
import { AgentModelAccessView } from "./AgentModelAccess";
import { TriggerBindingEditorView } from "./TriggerBindingEditor";

/**
 * The agent's Configuration tab on SettingsPage in single-section mode: the
 * nav lists every section and only the active one mounts. The webhooks
 * section is an app/ container with its own queries, so it stands in a
 * label here; the model toggles likewise.
 */
const meta: Meta = {
  title: "Screens/Agents/Detail/AgentConfigurationTab",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Slot({ label }: { label: string }) {
  return <div className="text-muted-foreground rounded-md border p-3 text-sm">{label}</div>;
}

const SLOTS: AgentConfigurationSlots = {
  build: <AgentBuildScreen {...BUILD} />,
  "run-mode": (
    <AgentControlScreen
      {...CONTROL}
      triggerEditor={<TriggerBindingEditorView {...TRIGGER_EDITOR} />}
    />
  ),
  config: <ConfigEditorScreen {...CONFIG_EDITOR} />,
  manifest: <ManifestPreviewScreen {...PREVIEW_PROPS} />,
  webhooks: <Slot label="CI / CD webhooks (WebhooksClient)" />,
  "model-access": (
    <AgentModelAccessView
      {...MODEL_ACCESS}
      managedModel={<Slot label="Managed model toggle (ManagedModelSection)" />}
      vncSession={<Slot label="VNC session toggle (VncSessionSection)" />}
    />
  ),
};

function Tab({
  initial = null,
  slots = SLOTS,
}: {
  initial?: string | null;
  slots?: AgentConfigurationSlots;
}) {
  return <AgentConfigurationTab section={useLocalSettingsSection(initial)} slots={slots} />;
}

/** Build is the default section; the nav lists the other five. */
export const Full: Story = {
  render: () => <Tab />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const nav = within(canvas.getByRole("navigation", { name: "Settings sections" }));
    await expect(nav.getAllByRole("link").length).toBe(6);
    await userEvent.click(nav.getByRole("link", { name: "Model access" }));
    await expect(
      await canvas.findByText("Managed model toggle (ManagedModelSection)")
    ).toBeInTheDocument();
  },
};

export const RunModeSection: Story = { render: () => <Tab initial="run-mode" /> };

export const ConfigSection: Story = { render: () => <Tab initial="config" /> };

export const ManifestSection: Story = { render: () => <Tab initial="manifest" /> };

export const Loading: Story = {
  render: () => <Tab slots={{ ...SLOTS, build: <AgentBuildScreen {...BUILD_LOADING} /> }} />,
};

export const Error: Story = {
  render: () => <Tab slots={{ ...SLOTS, build: <AgentBuildScreen {...BUILD_ERROR} /> }} />,
};

/** An unknown `?section=` shows the first section. */
export const UnknownSection: Story = { render: () => <Tab initial="no-such-section" /> };

export const LongStrings: Story = {
  render: () => <Tab slots={{ ...SLOTS, build: <AgentBuildScreen {...BUILD_LONG} /> }} />,
};

/** Below `md` the nav is a select; nothing scrolls sideways at 768px. */
export const At768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Tab slots={{ ...SLOTS, build: <AgentBuildScreen {...BUILD_LONG} /> }} />
    </div>
  ),
};

export const JapaneseConfiguration: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <Tab
        initial="model-access"
        slots={{
          build: <Slot label="LITERAL_BUILD" />,
          "model-access": <Slot label="LITERAL_MODEL" />,
        }}
      />
    </NextIntlClientProvider>
  ),
};
