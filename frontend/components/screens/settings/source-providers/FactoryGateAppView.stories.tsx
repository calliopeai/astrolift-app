import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, fn, userEvent, within } from "storybook/test";

import { FactoryGateAppView, type FactoryGateAppViewProps } from "./FactoryGateAppView";
import { RotateFactoryGateKeySheet } from "./RotateFactoryGateKeySheet";
import {
  FACTORY_GATE,
  factoryGateApp,
  NO_FACTORY_GATE_APP,
  ROTATE_FACTORY_GATE_KEY,
} from "./settings-source-providers.fixtures";

const meta: Meta = {
  title: "Screens/Settings/SourceProviders/FactoryGateAppView",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const onRegister = fn();

const renderRotateKey: FactoryGateAppViewProps["renderRotateKey"] = (open, onOpenChange, owner) => (
  <RotateFactoryGateKeySheet
    {...ROTATE_FACTORY_GATE_KEY}
    open={open}
    onOpenChange={onOpenChange}
    owner={owner}
  />
);

const view = (patch: Partial<FactoryGateAppViewProps> = {}) => (
  <FactoryGateAppView
    {...FACTORY_GATE}
    onRegister={onRegister}
    renderRotateKey={renderRotateKey}
    {...patch}
  />
);

/** Installed: the factory's workflows can mint Members read tokens. */
export const Installed: Story = { render: () => view() };

/** Nothing registered: the GitHub organization field and the register button. */
export const NotRegistered: Story = { render: () => view({ app: NO_FACTORY_GATE_APP }) };

/** Registered on GitHub, never installed: install it there. */
export const Registered: Story = {
  render: () => view({ app: factoryGateApp({ state: "REGISTERED", installationId: null }) }),
};

/** No stored key: no token can be minted until a new key is rotated in. */
export const MissingKey: Story = {
  render: () => view({ app: factoryGateApp({ state: "MISSING_KEY" }) }),
};

export const Loading: Story = { render: () => view({ app: null, loading: true }) };

export const LoadFailed: Story = {
  render: () => view({ app: null, error: { message: "Network error: failed to fetch" } }),
};

/** Viewer without scm.connect: the state and ids, no actions. */
export const ReadOnly: Story = { render: () => view(), globals: { permissions: "none" } };

export const LongStrings: Story = {
  render: () =>
    view({
      app: factoryGateApp({
        ownerLogin: "acme-corporation-platform-engineering-x",
        appId: "123456789012",
        installationId: "98765432109876543",
      }),
    }),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border">
      {view({ app: NO_FACTORY_GATE_APP })}
    </div>
  ),
};

/** The register button waits for a valid organization login, then hands it over. */
export const RegisterHandsOverTheOrganization: Story = {
  render: () => view({ app: NO_FACTORY_GATE_APP }),
  play: async ({ canvasElement }) => {
    onRegister.mockClear();
    const canvas = within(canvasElement);
    const register = canvas.getByRole("button", { name: "Register on GitHub" });
    await expect(register).toBeDisabled();
    await userEvent.type(canvas.getByLabelText("GitHub organization"), "acme corp");
    await expect(register).toBeDisabled();
    await userEvent.clear(canvas.getByLabelText("GitHub organization"));
    await userEvent.type(canvas.getByLabelText("GitHub organization"), "acme-corp");
    await userEvent.click(register);
    await expect(onRegister).toHaveBeenCalledWith("acme-corp");
  },
};

/** A pasted key leaves the form when the sheet closes. */
export const RotateDropsThePastedKey: Story = {
  render: () => view(),
  play: async ({ canvasElement }) => {
    const rotate = within(canvasElement).getByRole("button", { name: "Rotate key" });
    await userEvent.click(rotate);
    const sheet = within(document.body).getByRole("dialog", {
      name: "Rotate the factory gate key",
    });
    const field = within(sheet).getByLabelText("New private key (PEM)");
    await userEvent.type(field, "pasted-key");
    await userEvent.click(within(sheet).getByRole("button", { name: "Cancel" }));
    await userEvent.click(rotate);
    const reopened = within(document.body).getByRole("dialog", {
      name: "Rotate the factory gate key",
    });
    await expect(within(reopened).getByLabelText("New private key (PEM)")).toHaveValue("");
  },
};
