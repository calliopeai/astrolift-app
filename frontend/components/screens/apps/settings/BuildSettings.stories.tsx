import { NextIntlClientProvider } from "next-intl";
import french from "@/messages/fr.json";

import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { BUILD, BUILD_LONG, BUILD_ROOT } from "./app-settings-members.fixtures";
import { BuildSettingsView } from "./BuildSettings";

const meta: Meta<typeof BuildSettingsView> = {
  title: "Screens/Apps/Settings/BuildSettings",
  component: BuildSettingsView,
  args: BUILD,
};
export default meta;

type Story = StoryObj<typeof BuildSettingsView>;

/** A monorepo app building from its manifest's directory. */
export const Full: Story = {};

/** Registered before detection: the repository root. */
export const RepositoryRoot: Story = { args: BUILD_ROOT };

/** A save in flight: the fields and buttons are disabled. */
export const Loading: Story = { args: { saving: true } };

/** A blank stored context (never valid for a CI-built app) is refused in place. */
export const Empty: Story = {
  args: { buildContext: "", dockerfilePath: "" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getAllByRole("alert")).toHaveLength(2);
    await expect(canvas.getByRole("button", { name: "Save" })).toBeDisabled();
  },
};

/** Saved on a CI-built app: the workflow in the repo needs a sync. */
export const SavedNeedsCiSync: Story = {
  args: BUILD_ROOT,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const context = canvas.getByLabelText("Build context");
    await userEvent.clear(context);
    await userEvent.type(context, "src/checkout");
    await userEvent.click(canvas.getByRole("button", { name: "Save" }));
    await expect(await canvas.findByRole("status")).toHaveTextContent("astro ci setup checkout");
  },
};

/** Saved on a platform-built app: the next build reads the new paths. */
export const SavedPlatformBuild: Story = {
  args: { buildMode: "platform_build" },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Dockerfile"), ".prod");
    await userEvent.click(canvas.getByRole("button", { name: "Save" }));
    await expect(await canvas.findByRole("status")).toHaveTextContent(/next platform build/);
  },
};

/** A pre-built image: nothing is built from these paths. */
export const Prebuilt: Story = { args: { buildMode: "none", hasRepository: false } };

/** A rejected save keeps the edits and says why. */
export const SaveFailed: Story = {
  args: {
    onSave: async () => {
      throw new Error("buildContext must not escape the repository root, got '../outside'");
    },
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    const context = canvas.getByLabelText("Build context");
    await userEvent.clear(context);
    await userEvent.type(context, "../outside");
    await userEvent.click(canvas.getByRole("button", { name: "Save" }));
    await expect(await canvas.findByRole("alert")).toHaveTextContent(/escape the repository/);
    await expect(context).toHaveValue("../outside");
  },
};

export const LongStrings: Story = { args: BUILD_LONG };

export const Width768: Story = {
  args: BUILD_LONG,
  render: (args) => (
    <div style={{ width: 768 }}>
      <BuildSettingsView {...args} />
    </div>
  ),
};

export const FrenchWidth768: Story = {
  args: BUILD_LONG,
  render: (args) => (
    <NextIntlClientProvider locale="fr" messages={french}>
      <div style={{ width: 768 }}>
        <BuildSettingsView {...args} />
      </div>
    </NextIntlClientProvider>
  ),
};
