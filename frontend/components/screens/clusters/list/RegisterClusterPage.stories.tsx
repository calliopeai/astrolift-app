import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import { REGISTER } from "./fixtures";
import { RegisterClusterPage } from "./RegisterClusterPage";

const meta: Meta = {
  title: "Screens/Clusters/List/RegisterClusterPage",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const page = (patch: Partial<React.ComponentProps<typeof RegisterClusterPage>> = {}) => (
  <RegisterClusterPage {...REGISTER} {...patch} />
);

export const Cluster: Story = { render: () => page() };

export const Connection: Story = { render: () => page({ initialStep: 2 }) };

export const RegionsLoading: Story = {
  render: () => page({ regions: [], regionsLoading: true }),
};

/** No provider plugins registered: the step says so in place and cannot continue. */
export const NoPlugins: Story = { render: () => page({ plugins: [], pluginSlug: "" }) };

/** k8s_native has no region concept, so the picker is hidden. */
export const KubernetesNative: Story = { render: () => page({ pluginSlug: "k8s_native" }) };

export const Registering: Story = { render: () => page({ initialStep: 2, registering: true }) };

/** Errors the server returned, beside their fields. */
export const FieldErrors: Story = {
  render: () =>
    page({
      initialErrors: {
        slug: "A cluster with slug prd-us-west-2 already exists in this organization.",
        providerPluginSlug: "Provider plugin eks is disabled.",
      },
    }),
};

/** Continue with nothing filled in: the errors appear in place, not in a toast. */
export const ValidatesInPlace: Story = {
  render: () => page(),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await expect(await canvas.findByText("Give the cluster a name.")).toBeInTheDocument();
    await expect(canvas.getByLabelText("Display name")).toHaveAttribute("aria-invalid", "true");
  },
};

/** A refused register with no field: the error leads the form. */
export const FormError: Story = {
  render: () =>
    page({
      onRegister: async () => ({
        ok: false,
        fieldErrors: {},
        formError: "The cluster API server did not answer the capability probe.",
      }),
    }),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(canvas.getByLabelText("Display name"), "prd-us-west-2");
    await userEvent.click(canvas.getByRole("button", { name: /Continue/ }));
    await userEvent.click(await canvas.findByRole("button", { name: "Register cluster" }));
    await expect(
      await canvas.findByText("The cluster API server did not answer the capability probe.")
    ).toBeInTheDocument();
  },
};

export const LongStrings: Story = {
  render: () =>
    page({
      plugins: [
        {
          id: "pl-long",
          slug: "custom-provider-plugin-with-a-deliberately-long-slug",
          name: "A custom provider plugin with a deliberately long display name",
          version: "0.0.1",
          isEnabled: true,
          capabilitiesManifest: {},
        },
      ],
      pluginSlug: "custom-provider-plugin-with-a-deliberately-long-slug",
      regions: [
        {
          id: "ap-southeast-4-melbourne-local-zone-1a",
          label: "Asia Pacific (Melbourne) local zone with a long label",
          continent: "oceania",
        },
      ],
      initialErrors: {
        endpoint:
          "https://very-long-endpoint-name-0123456789abcdef.gr7.ap-southeast-4.eks.amazonaws.com/api/v1/namespaces/kube-system/pods?labelSelector=app%3Dcert-manager-webhook is not reachable from the control plane.",
        region:
          "arn:aws:eks:ap-southeast-4:123456789012:cluster/prd-us-west-2-tenant-shared-workloads-with-a-deliberately-long-name/nodegroup/general-purpose-arm64-graviton-spot-capacity-pool-0123456789abcdef",
      },
    }),
};

export const Width768: Story = {
  render: () => <div style={{ width: 768 }}>{page({ initialStep: 2 })}</div>,
};
