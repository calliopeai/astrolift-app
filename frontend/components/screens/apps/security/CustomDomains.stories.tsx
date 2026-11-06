import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { AccessCardView, AccessEditorView } from "./AccessCard";
import { CustomDomainsView } from "./CustomDomains";
import { ACCESS_OPEN, DOMAINS_MIXED, EDITOR, LONG } from "./app-security-previews.fixtures";

const meta: Meta<typeof CustomDomainsView> = {
  title: "Screens/Apps/Security/CustomDomains",
  component: CustomDomainsView,
  args: DOMAINS_MIXED,
};
export default meta;

type Story = StoryObj<typeof CustomDomainsView>;

/** One domain serving, one paused, with the toggle for an operator holding `app.access`. */
export const Mixed: Story = {};

export const Serving: Story = {
  args: { domains: [DOMAINS_MIXED.domains[0]] },
};

export const Paused: Story = {
  args: { domains: [DOMAINS_MIXED.domains[1]] },
};

/** The app has no custom domains. */
export const None: Story = { args: { domains: [] } };

export const Loading: Story = { args: { domains: [], loading: true } };

/** The list could not be loaded. */
export const LoadFailed: Story = { args: { domains: [], failed: true } };

/** Pausing asks first, and says visitors get a 503 until it is resumed. */
export const ConfirmPause: Story = {
  args: {
    control: {
      ...DOMAINS_MIXED.control,
      pausing: { id: "d1", hostname: "www.example.com" },
    },
  },
};

export const Saving: Story = {
  args: { control: { ...DOMAINS_MIXED.control, saving: true } },
};

/** A refusal from the server, shown under the list. */
export const Error: Story = {
  args: {
    control: { ...DOMAINS_MIXED.control, refusal: "this action needs a person" },
  },
};

/** Without `app.access`: the state only. */
export const ReadOnly: Story = { args: { control: undefined } };

export const LongStrings: Story = {
  args: { domains: [{ ...DOMAINS_MIXED.domains[0], hostname: `${LONG}.example.com` }] },
};

/** The toggles in their place: below the public switch on the app's Access tab. */
export const InTheAccessCard: Story = {
  render: () => (
    <AccessCardView
      {...ACCESS_OPEN}
      editor={<AccessEditorView {...EDITOR} groups={[]} users={[]} />}
      customDomains={<CustomDomainsView {...DOMAINS_MIXED} />}
    />
  ),
};
