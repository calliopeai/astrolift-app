import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AccessCardView, AccessEditorView } from "./AccessCard";
import {
  ACCESS_OPEN,
  EDITOR,
  SECURITY_HEADERS_OFF,
  SECURITY_HEADERS_ON,
} from "./app-security-previews.fixtures";
import { SecurityHeadersSwitchView } from "./SecurityHeadersSwitch";

const meta: Meta<typeof SecurityHeadersSwitchView> = {
  title: "Screens/Apps/Security/SecurityHeadersSwitch",
  component: SecurityHeadersSwitchView,
  args: SECURITY_HEADERS_OFF,
};
export default meta;

type Story = StoryObj<typeof SecurityHeadersSwitchView>;

/** The default: no headers added, with the switch for an operator holding `app.access`. */
export const Off: Story = {};

export const On: Story = { args: SECURITY_HEADERS_ON };

/** While the change is saving the button waits. */
export const Saving: Story = {
  args: { ...SECURITY_HEADERS_OFF, control: { ...SECURITY_HEADERS_OFF.control, saving: true } },
};

/** A refusal from the server shows under the button. */
export const ErrorState: Story = {
  args: {
    control: { ...SECURITY_HEADERS_OFF.control, error: "Change failed." },
  },
};

/** Without `app.access`: the state only. */
export const ReadOnly: Story = { args: { control: undefined } };

/** The switch in its place: under the access rule on the app's Access tab. */
export const InTheAccessCard: Story = {
  render: () => (
    <AccessCardView
      {...ACCESS_OPEN}
      editor={<AccessEditorView {...EDITOR} groups={[]} users={[]} />}
      securityHeadersSwitch={<SecurityHeadersSwitchView {...SECURITY_HEADERS_OFF} />}
    />
  ),
};
