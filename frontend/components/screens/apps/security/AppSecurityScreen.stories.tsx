import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AppTabsView } from "../detail/AppTabs";

import { AccessCardView, AccessEditorView } from "./AccessCard";
import {
  ACCESS_OPEN,
  EDITOR,
  SCAN_EVENT,
  SECURITY,
  SECURITY_LONG,
} from "./app-security-previews.fixtures";
import { AppSecurityScreen } from "./AppSecurityScreen";

const tabs = (
  <AppTabsView
    slug="checkout"
    basePath="/apps"
    pathname="/apps/checkout/security"
    active="security"
  />
);
const access = (
  <AccessCardView
    {...ACCESS_OPEN}
    editor={<AccessEditorView {...EDITOR} groups={[]} users={[]} />}
  />
);

const meta: Meta<typeof AppSecurityScreen> = {
  title: "Screens/Apps/Security/AppSecurityScreen",
  component: AppSecurityScreen,
  parameters: { layout: "fullscreen" },
  args: { ...SECURITY, tabs, access },
};
export default meta;

type Story = StoryObj<typeof AppSecurityScreen>;

/** Signed image, SBOM with a download, five findings sorted by severity. */
export const Full: Story = {};

export const Loading: Story = { args: { app: null, loading: true, tabs: null, access: null } };

/** The events are still loading: each card shows its skeleton. */
export const EventsLoading: Story = { args: { eventsLoading: true } };

/** No signing, SBOM or scan events for this app yet. */
export const Empty: Story = {
  args: { latestSigning: null, latestSbom: null, latestScan: null },
};

/** Scanned and clean: "no known vulnerabilities". */
export const CleanScan: Story = {
  args: {
    latestScan: {
      ...SCAN_EVENT,
      payload: {
        scanned_at: "2026-09-27T14:06:00Z",
        counts: { critical: 0, high: 0, medium: 0, low: 0 },
        findings: [],
      },
    },
  },
};

/**
 * The screen has no error state (a failed query reads as a missing app).
 * Closest real state: no app with this slug, or no permission to see it.
 */
export const NotFound: Story = {
  args: { app: null, slug: "no-such-app", tabs: null, access: null },
};

export const LongStrings: Story = { args: { ...SECURITY_LONG } };
