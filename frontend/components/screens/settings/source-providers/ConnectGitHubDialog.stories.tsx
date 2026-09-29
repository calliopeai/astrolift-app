import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ConnectGitHubDialogView } from "./ConnectGitHubDialog";
import { CONNECT_GITHUB_PROPS, LONG_TEXT } from "./settings-source-connect.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/ConnectGitHubDialog",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = async () => within(await within(document.body).findByRole("dialog"));

const openByo = async () => {
  const s = await sheet();
  await userEvent.click(s.getByRole("button", { name: /Connect an existing GitHub App/ }));
  return s;
};

/** The method picker: create a new App or bring your own. */
export const Empty: Story = {
  render: () => <ConnectGitHubDialogView {...CONNECT_GITHUB_PROPS} />,
};

/** Bootstrap step: org slug and the permissions Astrolift will request. */
export const Bootstrap: Story = {
  render: () => <ConnectGitHubDialogView {...CONNECT_GITHUB_PROPS} />,
  play: async () => {
    const s = await sheet();
    await userEvent.click(s.getByRole("button", { name: /Create a new GitHub App/ }));
    await expect(s.getByRole("button", { name: "Continue on GitHub →" })).toBeEnabled();
  },
};

/** BYO verify in flight: the submit reads "Verifying…". */
export const Loading: Story = {
  render: () => <ConnectGitHubDialogView {...CONNECT_GITHUB_PROPS} loading />,
  play: async () => {
    const s = await openByo();
    await expect(s.getByRole("button", { name: "Verifying…" })).toBeDisabled();
  },
};

/** BYO form filled: App ID, PEM, Client ID, org. */
export const Full: Story = {
  render: () => <ConnectGitHubDialogView {...CONNECT_GITHUB_PROPS} />,
  play: async () => {
    const s = await openByo();
    await userEvent.type(s.getByLabelText("App ID"), "3705068");
    await userEvent.type(s.getByLabelText("Private key (PEM)"), "-----BEGIN RSA PRIVATE KEY-----");
    await userEvent.type(s.getByLabelText(/Client ID/), "Iv23lic8662KXwe4XKEI");
    await userEvent.type(s.getByLabelText(/GitHub org \/ user login/), "acme-corp");
    await expect(s.getByRole("button", { name: "Connect App" })).toBeEnabled();
  },
};

/** Inline validation: a non-numeric App ID and a malformed Client ID. */
export const InvalidInput: Story = {
  render: () => <ConnectGitHubDialogView {...CONNECT_GITHUB_PROPS} />,
  play: async () => {
    const s = await openByo();
    await userEvent.type(s.getByLabelText("App ID"), "Iv23lic8662");
    await userEvent.type(s.getByLabelText(/Client ID/), "not-a-client-id");
    await expect(s.getByText("The App ID is the numeric id, e.g. 3705068.")).toBeInTheDocument();
    await expect(s.getByRole("button", { name: "Connect App" })).toBeDisabled();
  },
};

/** Bootstrap with an org slug over GitHub's 39-character limit. */
export const LongStrings: Story = {
  render: () => <ConnectGitHubDialogView {...CONNECT_GITHUB_PROPS} />,
  play: async () => {
    const s = await sheet();
    await userEvent.click(s.getByRole("button", { name: /Create a new GitHub App/ }));
    await userEvent.type(s.getByLabelText("Where should the App live?"), LONG_TEXT);
    await expect(
      s.getByText("GitHub org slugs are alphanumeric + hyphen, max 39 characters.")
    ).toBeInTheDocument();
  },
};
