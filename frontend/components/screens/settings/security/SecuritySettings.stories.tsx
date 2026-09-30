import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { SecuritySettingsView as View, type SecuritySettingsViewProps } from "./SecuritySettings";
import { SESSIONS_LIST } from "./sessions-list";
import {
  SECURITY_LONG_SESSIONS,
  SESSIONS,
  securityProps,
} from "./settings-security-notifications.fixtures";

const meta: Meta = {
  title: "Screens/Settings/Security/SecuritySettings",
};
export default meta;

type Story = StoryObj;

/** The view with its list state in memory, as the hook keeps it. */
function SecuritySettingsView(props: SecuritySettingsViewProps) {
  const list = useLocalListState(SESSIONS_LIST);
  return <View {...props} list={list} />;
}

export const Full: Story = {
  render: () => <SecuritySettingsView {...securityProps()} />,
};

export const Loading: Story = {
  render: () => <SecuritySettingsView {...securityProps({ sessions: [], loading: true })} />,
};

export const Empty: Story = {
  render: () => <SecuritySettingsView {...securityProps({ sessions: [] })} />,
};

/** The sessions query failed. */
export const LoadFailed: Story = {
  render: () => (
    <SecuritySettingsView
      {...securityProps({
        sessions: [],
        errorMessage: "Response not successful: Received status code 503",
      })}
    />
  ),
};

/** Only the current session: "Sign out everywhere" is disabled. */
export const OnlyThisSession: Story = {
  render: () => (
    <SecuritySettingsView {...securityProps({ sessions: SESSIONS.filter((s) => s.isCurrent) })} />
  ),
  play: async ({ canvasElement }) => {
    await expect(
      within(canvasElement).getByRole("button", { name: "Sign out everywhere" })
    ).toBeDisabled();
  },
};

export const SigningOut: Story = {
  render: () => <SecuritySettingsView {...securityProps({ signingOut: true })} />,
};

export const LongStrings: Story = {
  render: () => <SecuritySettingsView {...securityProps({ sessions: SECURITY_LONG_SESSIONS })} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      <SecuritySettingsView {...securityProps({ sessions: SECURITY_LONG_SESSIONS })} />
    </div>
  ),
};
