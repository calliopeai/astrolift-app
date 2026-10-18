import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { TEMPORARY_RULES } from "../apps/temporary-app.fixtures";

import { HOST_PROJECTS } from "./brains.fixtures";
import { HostBrainAppDialog } from "./HostBrainAppDialog";

const meta: Meta<typeof HostBrainAppDialog> = {
  title: "Screens/Brains/HostBrainAppDialog",
  component: HostBrainAppDialog,
  args: {
    brainSlug: "leo-brain",
    projects: HOST_PROJECTS,
    submitting: false,
    temporary: { rules: TEMPORARY_RULES, loading: false, failed: false },
    onSubmit: fn(),
    onDismiss: fn(),
  },
};
export default meta;

type Story = StoryObj<typeof HostBrainAppDialog>;

/** The image is required; the first project and port 8080 are the defaults. */
export const Create: Story = {
  play: async ({ args }) => {
    const body = within(document.body);
    const create = await body.findByRole("button", { name: "Create app" });
    await expect(create).toBeDisabled();
    await userEvent.type(body.getByLabelText("Image"), "ghcr.io/acme/brain-chat:1.0");
    await userEvent.click(create);
    await expect(args.onSubmit).toHaveBeenCalledWith({
      projectId: HOST_PROJECTS[0].id,
      image: "ghcr.io/acme/brain-chat:1.0",
      name: "",
      port: 8080,
      expiresIn: "",
      deleteDataOnExpiry: false,
    });
  },
};

/** A port outside 1 to 65535 cannot be submitted. */
export const BadPort: Story = {
  play: async () => {
    const body = within(document.body);
    await userEvent.type(await body.findByLabelText("Image"), "ghcr.io/acme/brain-chat:1.0");
    const port = body.getByLabelText("Port");
    await userEvent.clear(port);
    await userEvent.type(port, "70000");
    await expect(body.getByRole("button", { name: "Create app" })).toBeDisabled();
  },
};

export const Submitting: Story = { args: { submitting: true } };

export const NoProjects: Story = { args: { projects: [] } };

export const LongSlug: Story = {
  args: {
    brainSlug: "a-very-long-semantic-layer-slug-that-keeps-going-for-the-layout-check",
    projects: [
      {
        id: "p-long",
        label: "platform-engineering-and-reliability/knowledge-and-ontology-services",
      },
      ...HOST_PROJECTS,
    ],
  },
};

export const Closed: Story = { args: { brainSlug: null } };

export const Temporary: Story = {
  play: async () => {
    const body = within(document.body);
    await userEvent.type(await body.findByLabelText("App lifetime"), "7d");
    await expect(body.getByLabelText("Delete managed data when the app expires")).toBeVisible();
  },
};
export const TemporaryRequired: Story = {
  args: {
    temporary: {
      rules: { ...TEMPORARY_RULES, permanentAllowed: false },
      loading: false,
      failed: false,
    },
  },
};
export const PolicyUnavailable: Story = {
  args: { temporary: { rules: null, loading: false, failed: true, onRetry: fn() } },
};
