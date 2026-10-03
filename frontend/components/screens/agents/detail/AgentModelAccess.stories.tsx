import { NextIntlClientProvider } from "next-intl";
import fr from "@/messages/fr.json";
import ja from "@/messages/ja.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LONG, MODEL_ACCESS } from "./agent-detail-shell.fixtures";
import { AgentModelAccessView } from "./AgentModelAccess";

/**
 * The real toggles (`ManagedModelSection`, `VncSessionSection`) live under
 * app/ and are passed in as slots; these stories stand in a plain label.
 */
function Slot({ label }: { label: string }) {
  return <div className="text-muted-foreground rounded-md border p-3 text-sm">{label}</div>;
}

const meta: Meta<typeof AgentModelAccessView> = {
  title: "Screens/Agents/Detail/AgentModelAccess",
  component: AgentModelAccessView,
  args: {
    ...MODEL_ACCESS,
    managedModel: <Slot label="Managed model toggle (ManagedModelSection)" />,
    vncSession: <Slot label="VNC session toggle (VncSessionSection)" />,
  },
};
export default meta;

type Story = StoryObj<typeof AgentModelAccessView>;

export const Full: Story = {};

export const Loading: Story = {
  args: { spec: null, loading: true },
};

/** No active org yet: also a skeleton. */
export const NoOrg: Story = {
  args: { spec: null, orgId: "" },
};

/**
 * No environment spec was returned by a successful read.
 */
export const Empty: Story = {
  args: { spec: null },
};

export const LongStrings: Story = {
  args: {
    spec: { ...MODEL_ACCESS.spec!, slug: LONG, name: LONG },
    managedModel: <Slot label={LONG} />,
    vncSession: <Slot label={LONG} />,
  },
};

export const FrenchEmpty: Story = {
  render: () => (
    <NextIntlClientProvider locale="fr" messages={fr}>
      <AgentModelAccessView {...MODEL_ACCESS} spec={null} />
    </NextIntlClientProvider>
  ),
};
export const JapaneseReadFailed: Story = {
  render: () => (
    <NextIntlClientProvider locale="ja" messages={ja}>
      <AgentModelAccessView
        {...MODEL_ACCESS}
        spec={null}
        error="RAW_ENV_SPEC_DIAGNOSTIC"
        onRetry={() => {}}
      />
    </NextIntlClientProvider>
  ),
};
