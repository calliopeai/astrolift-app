import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { EMPTY_MODEL, LONG_MODEL, MODEL, MODEL_ERRORS } from "./app-config-manifest.fixtures";
import { ManifestFormBuilder } from "./ManifestForm";

const meta: Meta<typeof ManifestFormBuilder> = {
  title: "Screens/Apps/Config/ManifestForm",
  component: ManifestFormBuilder,
  args: { model: MODEL, errors: [], onChange: () => {} },
};
export default meta;

type Story = StoryObj<typeof ManifestFormBuilder>;

/** Workloads (a service and a cronjob), env, a managed service, preserved keys. */
export const Full: Story = {};

/** The builder has no loading state; a blank manifest. */
export const Empty: Story = {
  args: { model: EMPTY_MODEL },
};

/** The error state: inline field errors and invalid entries outlined. */
export const WithErrors: Story = {
  args: { errors: MODEL_ERRORS },
};

export const LongStrings: Story = {
  args: { model: LONG_MODEL },
};
