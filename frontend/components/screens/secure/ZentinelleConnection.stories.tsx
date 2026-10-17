import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { ZentinelleConnectForm, ZentinelleConnectionCard } from "./ZentinelleConnection";
import { HEALTHY_CONNECTION, REVOKED_CONNECTION } from "./zentinelle-fixtures";

const meta: Meta = {
  title: "Screens/Secure/Zentinelle/Connection",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const ok = async () => null;

export const ConnectForm: Story = {
  render: () => <ZentinelleConnectForm connecting={false} onConnect={ok} />,
};

/** Submitting empty asks for both fields, without calling the server. */
export const ConnectFormRequired: Story = {
  render: () => <ZentinelleConnectForm connecting={false} onConnect={ok} />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(canvas.getByRole("button"));
    await expect(await canvas.findByRole("alert")).toBeInTheDocument();
  },
};

export const ConnectFormRefused: Story = {
  render: () => (
    <ZentinelleConnectForm
      connecting={false}
      onConnect={async () => "the enrollment code was already used"}
    />
  ),
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.type(
      canvas.getByLabelText(/URL/, { selector: "#zentinelle-url" }),
      "https://z.example.com"
    );
    await userEvent.type(canvas.getByLabelText(/code/i), "ABCD-1234");
    await userEvent.click(canvas.getByRole("button"));
    await expect(
      await canvas.findByText("the enrollment code was already used")
    ).toBeInTheDocument();
  },
};

export const Connecting: Story = {
  render: () => <ZentinelleConnectForm connecting onConnect={ok} />,
};

export const Card: Story = {
  render: () => (
    <ZentinelleConnectionCard
      connection={HEALTHY_CONNECTION}
      canConnect
      busy={false}
      onSetGatewayUrl={ok}
      onDisconnect={ok}
    />
  ),
};

export const CardReadOnly: Story = {
  render: () => (
    <ZentinelleConnectionCard
      connection={HEALTHY_CONNECTION}
      canConnect={false}
      busy={false}
      onSetGatewayUrl={ok}
      onDisconnect={ok}
    />
  ),
};

export const CardRevoked: Story = {
  render: () => (
    <ZentinelleConnectionCard
      connection={REVOKED_CONNECTION}
      canConnect
      busy={false}
      onSetGatewayUrl={ok}
      onDisconnect={ok}
    />
  ),
};
