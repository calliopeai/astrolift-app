import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import en from "@/messages/en.json";
import { DomainDefaultSettings, type DomainDefaultSettingsProps } from "./DomainDefaultSettings";
export const DEFAULT_SETTINGS: DomainDefaultSettingsProps = {
  zone: "new.example",
  version: 2,
  defaultsFingerprint: "a".repeat(64),
  declared: "tenant_apps",
  effectiveTenant: "old.example",
  effectivePreview: "preview.example",
  canUpdate: true,
  canSetDefaults: true,
  loading: false,
  busy: false,
  reason: null,
  uncertain: false,
  onSave: async () => {},
  onRefresh: () => {},
};
const meta: Meta<typeof DomainDefaultSettings> = {
  title: "Screens/Domains/DomainDefaultSettings",
  component: DomainDefaultSettings,
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
export default meta;
type Story = StoryObj<typeof meta>;
export const DeclaredButNotEffective: Story = { args: DEFAULT_SETTINGS };
export const Effective: Story = { args: { ...DEFAULT_SETTINGS, effectiveTenant: "new.example" } };
export const Loading: Story = {
  args: { ...DEFAULT_SETTINGS, loading: true, effectiveTenant: null, effectivePreview: null },
};
export const Override: Story = {
  args: { ...DEFAULT_SETTINGS, canSetDefaults: false, reason: "ORGANIZATION_DEFAULT_OVERRIDE" },
};
export const ReadOnly: Story = {
  args: {
    ...DEFAULT_SETTINGS,
    canUpdate: false,
    canSetDefaults: false,
    reason: "READ_ONLY_DOMAIN",
  },
};
export const UnknownReply: Story = { args: { ...DEFAULT_SETTINGS, uncertain: true } };
export const RemoveOnly: Story = {
  args: { ...DEFAULT_SETTINGS, canSetDefaults: false, reason: "DOMAIN_WRITER_UNAVAILABLE" },
};

export const AcceptedAndRefreshed: Story = {
  args: {
    ...DEFAULT_SETTINGS,
    actionMessage: en.domainDefaults.applied,
    effectiveTenant: "new.example",
  },
};
export const ImpactChanged: Story = {
  args: { ...DEFAULT_SETTINGS, uncertain: true, actionError: en.domainDefaults.reasonChanged },
};
