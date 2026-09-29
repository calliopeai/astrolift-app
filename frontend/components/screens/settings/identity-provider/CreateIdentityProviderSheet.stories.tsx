import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { CreateIdentityProviderSheet } from "./CreateIdentityProviderSheet";
import { CREATE_SHEET } from "./settings-identity-provider.fixtures";

const meta: Meta = {
  title: "Screens/Settings/IdentityProvider/CreateIdentityProviderSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

/** Open with the default Generic OIDC kind and an empty `{}` config. */
export const Open: Story = { render: () => <CreateIdentityProviderSheet {...CREATE_SHEET} /> };

/** Save in flight: the submit button reads "Saving…" and is disabled. */
export const Creating: Story = {
  render: () => <CreateIdentityProviderSheet {...CREATE_SHEET} creating />,
};
