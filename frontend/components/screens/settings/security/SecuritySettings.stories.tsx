import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { SecuritySettingsView } from "./SecuritySettings";
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
};

export const SigningOut: Story = {
  render: () => <SecuritySettingsView {...securityProps({ signingOut: true })} />,
};

export const LongStrings: Story = {
  render: () => <SecuritySettingsView {...securityProps({ sessions: SECURITY_LONG_SESSIONS })} />,
};
