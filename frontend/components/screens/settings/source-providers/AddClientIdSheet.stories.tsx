import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import { AddClientIdSheet } from "./AddClientIdSheet";
import { ADD_CLIENT_ID, CONNECTIONS, LONG_CONNECTIONS } from "./settings-source-providers.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/AddClientIdSheet",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = (patch: Partial<React.ComponentProps<typeof AddClientIdSheet>> = {}) => (
  <AddClientIdSheet {...ADD_CLIENT_ID} connection={CONNECTIONS[1]} onClose={() => {}} {...patch} />
);

/** A GitHub App connection with no Client ID yet: the input starts empty. */
export const Open: Story = { render: () => sheet() };

/** The connection already carries a Client ID: it is prefilled. */
export const Prefilled: Story = { render: () => sheet({ connection: CONNECTIONS[0] }) };

/** The mutation is in flight. The sheet has no loading or empty data state. */
export const Saving: Story = {
  render: () => sheet({ connection: CONNECTIONS[0], saving: true }),
};

/** A pasted value that fails the Client ID pattern. */
export const InvalidClientId: Story = {
  render: () =>
    sheet({
      connection: { ...CONNECTIONS[0], appClientId: "12345" },
    }),
};

export const LongStrings: Story = { render: () => sheet({ connection: LONG_CONNECTIONS[0] }) };
