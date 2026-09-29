import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import type * as React from "react";
import { expect, within } from "storybook/test";

import { AppChromeProvider } from "@/app/(app)/apps/[slug]/components/app-chrome-context";
import { useLocalListState } from "@/components/list/use-list-state";
import { useLocalSettingsSection } from "@/components/settings/use-settings-section";

import { TOKENS_LONG, TOKENS_SCREEN } from "../secrets/app-secrets-tokens.fixtures";
import { DeployTokensScreen } from "../secrets/DeployTokensScreen";
import { APP_DEPLOY_TOKENS_LIST } from "../secrets/deploy-tokens-list";
import { ACCESS_ROWS_LONG, membersProps } from "../settings/app-access-members.fixtures";
import { APP_ACCESS_LIST } from "../settings/app-access-rows";
import { APP as MEMBERS_APP, LONG } from "../settings/app-settings-members.fixtures";
import { AppMembersScreen, type AppMembersScreenProps } from "../settings/AppMembersScreen";

import { AccessCardView, AccessEditorView } from "./AccessCard";
import {
  ACCESS_LONG,
  ACCESS_RESTRICTED,
  EDITOR,
  SECURITY,
  SECURITY_LONG,
} from "./app-security-previews.fixtures";
import { type AppAccessAccess, AppAccessScreen, type AppAccessSlots } from "./AppAccessScreen";
import { AppSecurityScreen } from "./AppSecurityScreen";

/**
 * The Access tab inside the app frame. The sections are the existing
 * screens, rendered framed (no title of their own), as the route fills them.
 */
const meta: Meta = {
  title: "Screens/Apps/Security/AppAccessScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const ALL: AppAccessAccess = { members: true, tokens: true, security: true, edge: true };

/** People with access around a list controller of its own, as the route's client has. */
function Members(overrides: Partial<AppMembersScreenProps>) {
  const list = useLocalListState(APP_ACCESS_LIST);
  return <AppMembersScreen {...membersProps(list, overrides)} />;
}

/** Deploy tokens around a list controller of its own, as the route's client has. */
function Tokens(props: Omit<React.ComponentProps<typeof DeployTokensScreen>, "list">) {
  const list = useLocalListState(APP_DEPLOY_TOKENS_LIST);
  return <DeployTokensScreen {...props} list={list} />;
}

const SLOTS: AppAccessSlots = {
  members: <Members />,
  tokens: <Tokens {...TOKENS_SCREEN} tabs={null} />,
  security: <AppSecurityScreen {...SECURITY} />,
  edge: <AccessCardView {...ACCESS_RESTRICTED} editor={<AccessEditorView {...EDITOR} />} />,
};

function Tab({
  initial = null,
  access = ALL,
  slots = SLOTS,
  restrictedMode = "show",
}: {
  initial?: string | null;
  access?: AppAccessAccess;
  slots?: AppAccessSlots;
  restrictedMode?: "show" | "hide";
}) {
  const section = useLocalSettingsSection(initial);
  return (
    <AppChromeProvider framed>
      <AppAccessScreen
        section={section}
        access={access}
        slots={slots}
        restrictedMode={restrictedMode}
      />
    </AppChromeProvider>
  );
}

/** Members, the default section. */
export const Full: Story = {
  render: () => <Tab />,
  play: async ({ canvasElement }) => {
    const nav = within(canvasElement).getByRole("navigation", { name: "Settings sections" });
    await expect(within(nav).getAllByText("Deploy tokens").length).toBeGreaterThan(0);
    await expect(within(nav).getAllByText("Edge access").length).toBeGreaterThan(0);
  },
};

export const DeployTokens: Story = { render: () => <Tab initial="tokens" /> };

export const SecurityScans: Story = { render: () => <Tab initial="security" /> };

export const EdgeAccess: Story = { render: () => <Tab initial="edge" /> };

/** A viewer without the permissions: the same sections, disabled, naming each one. */
export const ReadOnly: Story = {
  render: () => <Tab access={{ members: false, tokens: false, security: false, edge: false }} />,
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("org.manage_members")).toBeInTheDocument();
  },
};

/** The person hides what they can't change: those sections leave the nav. */
export const HiddenWhenRestricted: Story = {
  render: () => (
    <Tab
      restrictedMode="hide"
      access={{ members: false, tokens: true, security: false, edge: true }}
    />
  ),
};

/** Nothing the viewer may see or change, with hiding on. */
export const Empty: Story = {
  render: () => (
    <Tab
      restrictedMode="hide"
      access={{ members: false, tokens: false, security: false, edge: false }}
    />
  ),
};

/** The active section's own loading state. */
export const Loading: Story = {
  render: () => (
    <Tab
      slots={{
        ...SLOTS,
        members: <Members rows={[]} rowsLoading />,
      }}
    />
  ),
};

/** The active section's own error state. */
export const LoadFailed: Story = {
  render: () => (
    <Tab
      initial="tokens"
      slots={{
        ...SLOTS,
        tokens: <Tokens {...TOKENS_SCREEN} tabs={null} error={{ message: "upstream timed out" }} />,
      }}
    />
  ),
};

const LONG_SLOTS: AppAccessSlots = {
  members: <Members app={{ ...MEMBERS_APP, slug: LONG }} slug={LONG} rows={ACCESS_ROWS_LONG} />,
  tokens: <Tokens {...TOKENS_LONG} tabs={null} />,
  security: <AppSecurityScreen {...SECURITY_LONG} />,
  edge: <AccessCardView {...ACCESS_LONG} />,
};

export const LongStrings: Story = { render: () => <Tab slots={LONG_SLOTS} /> };

export const LongStringsEdge: Story = { render: () => <Tab initial="edge" slots={LONG_SLOTS} /> };

function At768({ children }: { children: React.ReactNode }) {
  return <div style={{ width: 768 }}>{children}</div>;
}

export const Width768: Story = {
  render: () => (
    <At768>
      <Tab slots={LONG_SLOTS} initial="tokens" />
    </At768>
  ),
};
