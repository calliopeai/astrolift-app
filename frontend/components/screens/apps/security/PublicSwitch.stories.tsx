import { NextIntlClientProvider } from "next-intl";
import de from "@/messages/de.json";
import ko from "@/messages/ko.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AccessCardView, AccessEditorView } from "./AccessCard";
import { LONG, ACCESS_OPEN, EDITOR, PUBLIC_OFF, PUBLIC_ON } from "./app-security-previews.fixtures";
import { PublicSwitchView } from "./PublicSwitch";

const meta: Meta<typeof PublicSwitchView> = {
  title: "Screens/Apps/Security/PublicSwitch",
  component: PublicSwitchView,
  args: PUBLIC_OFF,
};
export default meta;

type Story = StoryObj<typeof PublicSwitchView>;

/** The default: behind the sign-in, with the switch for an operator holding `app.access`. */
export const Off: Story = {};

/** Public on the Envoy cluster; the others say why they keep it behind the sign-in. */
export const On: Story = { args: PUBLIC_ON };

/** Without `app.access`: the state only. */
export const ReadOnly: Story = { args: { control: undefined } };

/** An access list only applies behind the sign-in, so the switch waits for it to go. */
export const Restricted: Story = {
  args: {
    access: {
      ...PUBLIC_OFF.access,
      restricted: true,
      publicStates: [{ clusterSlug: "astrolift-conflict", state: "restricted" }],
    },
  },
};

/** Making it public asks first, and says only the app's own sign-in protects it. */
export const Confirm: Story = { args: { control: { ...PUBLIC_OFF.control, confirming: true } } };

/** A refusal from the server: here, an agent the organization does not allow. */
export const Refused: Story = {
  args: {
    control: {
      ...PUBLIC_OFF.control,
      refusal: "this action needs a person: run `astro app public on` or use the app's Access tab",
    },
  },
};

export const Saving: Story = {
  args: { ...PUBLIC_ON, control: { ...PUBLIC_ON.control, saving: true } },
};

export const LongStrings: Story = {
  args: {
    access: {
      ...PUBLIC_ON.access,
      appSlug: LONG,
      publicStates: [{ clusterSlug: LONG, state: "public" }],
    },
  },
};

/** The switch in its place: under the access rule on the app's Access tab. */
export const InTheAccessCard: Story = {
  render: () => (
    <AccessCardView
      {...ACCESS_OPEN}
      editor={<AccessEditorView {...EDITOR} groups={[]} users={[]} />}
      publicSwitch={<PublicSwitchView {...PUBLIC_OFF} />}
    />
  ),
};

export const German: Story = {
  args: PUBLIC_ON,
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};

export const Korean: Story = {
  args: { control: { ...PUBLIC_OFF.control, confirming: true } },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ko" messages={ko} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
};
