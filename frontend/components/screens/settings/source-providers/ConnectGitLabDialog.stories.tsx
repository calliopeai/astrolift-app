import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ConnectGitLabDialogView } from "./ConnectGitLabDialog";
import {
  CONNECT_GITLAB_PROPS,
  LONG_CALLBACK_URL,
  LONG_TEXT,
} from "./settings-source-connect.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/ConnectGitLabDialog",
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj;

const sheet = async () => within(await within(document.body).findByRole("dialog"));

/** Fresh sheet: SaaS gitlab.com, user-level Applications link. */
export const Empty: Story = {
  render: () => <ConnectGitLabDialogView {...CONNECT_GITLAB_PROPS} />,
};

/** Mutation in flight: the submit reads "Connecting…". */
export const Loading: Story = {
  render: () => <ConnectGitLabDialogView {...CONNECT_GITLAB_PROPS} loading />,
  play: async () => {
    await expect((await sheet()).getByRole("button", { name: "Connecting…" })).toBeDisabled();
  },
};

/** Self-hosted group with the Application ID + Secret pasted back. */
export const Full: Story = {
  render: () => <ConnectGitLabDialogView {...CONNECT_GITLAB_PROPS} />,
  play: async () => {
    const s = await sheet();
    await userEvent.type(s.getByLabelText("GitLab group (optional)"), "acme-corp");
    await userEvent.type(
      s.getByLabelText("GitLab instance URL (self-hosted)"),
      "https://gitlab.acme.example"
    );
    await userEvent.type(s.getByLabelText("Application ID"), "a1b2c3d4e5");
    await userEvent.type(s.getByLabelText("Secret"), "gloas-example");
    await expect(s.getByRole("button", { name: "Save & connect" })).toBeEnabled();
  },
};

/**
 * No inline error state exists (a failed connect or copy is a toast), so
 * this shows the closest real one: Secret missing, submit disabled.
 */
export const MissingSecret: Story = {
  render: () => <ConnectGitLabDialogView {...CONNECT_GITLAB_PROPS} />,
  play: async () => {
    const s = await sheet();
    await userEvent.type(s.getByLabelText("Application ID"), "a1b2c3d4e5");
    await expect(s.getByRole("button", { name: "Save & connect" })).toBeDisabled();
  },
};

export const LongStrings: Story = {
  render: () => (
    <ConnectGitLabDialogView {...CONNECT_GITLAB_PROPS} callbackUrl={LONG_CALLBACK_URL} />
  ),
  play: async () => {
    const s = await sheet();
    await userEvent.type(s.getByLabelText("GitLab group (optional)"), LONG_TEXT);
  },
};
