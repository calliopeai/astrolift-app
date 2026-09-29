import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { PairDeviceView } from "./PairDevice";
import {
  QR_EXPIRED,
  QR_LONG,
  QR_PAYLOAD,
  pairProps,
} from "./settings-security-notifications.fixtures";

const meta: Meta = {
  title: "Screens/Settings/Security/PairDevice",
};
export default meta;

type Story = StoryObj;

/** Opens the pairing dialog, which renders in a portal on the body. */
async function openDialog(canvasElement: HTMLElement) {
  await userEvent.click(within(canvasElement).getByRole("button", { name: /Pair new device/ }));
  await expect(
    await within(canvasElement.ownerDocument.body).findByRole("dialog")
  ).toBeInTheDocument();
}

/** The card alone, before a pairing is started. */
export const Closed: Story = {
  render: () => <PairDeviceView {...pairProps()} />,
};

/** Dialog open, no QR minted yet: the label form. */
export const Empty: Story = {
  render: () => <PairDeviceView {...pairProps()} />,
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

/** The mint in flight. */
export const Loading: Story = {
  render: () => <PairDeviceView {...pairProps({ loading: true })} />,
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

export const Full: Story = {
  render: () => <PairDeviceView {...pairProps({ payload: QR_PAYLOAD })} />,
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

/** The QR outlived its TTL; the operator mints a new one. */
export const Expired: Story = {
  render: () => <PairDeviceView {...pairProps({ payload: QR_EXPIRED })} />,
  play: async ({ canvasElement }) => openDialog(canvasElement),
};

export const LongStrings: Story = {
  render: () => <PairDeviceView {...pairProps({ payload: QR_LONG })} />,
  play: async ({ canvasElement }) => openDialog(canvasElement),
};
