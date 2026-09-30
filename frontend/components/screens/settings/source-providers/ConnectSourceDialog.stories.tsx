import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ConnectSourceDialogView } from "./ConnectSourceDialog";
import { CONNECT_SOURCE_PROPS, LONG_TEXT } from "./settings-source-connect.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/ConnectSourceDialog",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = async () => within(await within(document.body).findByRole("dialog"));

/** Fresh sheet: GitHub PAT kind, nothing typed, Connect disabled. */
export const Empty: Story = {
  render: () => <ConnectSourceDialogView {...CONNECT_SOURCE_PROPS} />,
};

/** Mutation in flight: the submit reads "Connecting…". */
export const Loading: Story = {
  render: () => <ConnectSourceDialogView {...CONNECT_SOURCE_PROPS} loading />,
  play: async () => {
    await expect((await sheet()).getByRole("button", { name: "Connecting…" })).toBeDisabled();
  },
};

/** Every PAT field filled and a scope picked: Connect enabled. */
export const Full: Story = {
  render: () => <ConnectSourceDialogView {...CONNECT_SOURCE_PROPS} />,
  play: async () => {
    const s = await sheet();
    await userEvent.type(s.getByLabelText("Display name (optional)"), "Production GitHub");
    await userEvent.type(s.getByLabelText("Account / org login"), "acme-org");
    await userEvent.type(s.getByLabelText("Personal Access Token / app password"), "ghp_example");
    await userEvent.click(s.getByLabelText("private repos in connected org"));
    await expect(s.getByRole("button", { name: "Connect" })).toBeEnabled();
  },
};

/**
 * The sheet has no inline error state (a failed connect is a toast), so this
 * shows the closest real one: no secret yet, Connect stays disabled.
 */
export const MissingSecret: Story = {
  render: () => <ConnectSourceDialogView {...CONNECT_SOURCE_PROPS} />,
  play: async () => {
    const s = await sheet();
    await userEvent.type(s.getByLabelText("Account / org login"), "acme-org");
    await expect(s.getByRole("button", { name: "Connect" })).toBeDisabled();
  },
};

export const LongStrings: Story = {
  render: () => <ConnectSourceDialogView {...CONNECT_SOURCE_PROPS} />,
  play: async () => {
    const s = await sheet();
    const displayName = s.getByLabelText("Display name (optional)");
    const account = s.getByLabelText("Account / org login");
    await userEvent.click(displayName);
    await userEvent.paste(LONG_TEXT);
    await userEvent.click(account);
    await userEvent.paste(LONG_TEXT);
    await expect(displayName).toHaveValue(LONG_TEXT);
    await expect(account).toHaveValue(LONG_TEXT);
  },
};
