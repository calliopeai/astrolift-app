import { NextIntlClientProvider } from "next-intl";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, within } from "storybook/test";
import en from "@/messages/en.json";
import { DnsDelegationPanel, type DnsDelegationPanelProps } from "./DnsDelegationPanel";

const READ_ONLY = {
  id: "66666666-6666-4666-8666-666666666666",
  version: 3,
  name: "Public DNS",
  state: "ACTIVE",
  dnsWritesSupported: false,
  delegationZoneName: "",
};
const GRANTED = {
  ...READ_ONLY,
  id: "77777777-7777-4777-8777-777777777777",
  version: 4,
  name: "conflict.cloud writer",
  dnsWritesSupported: true,
  delegationZoneName: "conflict.cloud",
};
/** #1787: unbound, a read connection chosen, its parent zone detected. */
export const DELEGATION: DnsDelegationPanelProps = {
  zone: "astro.conflict.cloud",
  boundConnectionId: null,
  boundZoneName: "",
  lastWrite: null,
  canWrite: true,
  busy: false,
  message: null,
  error: null,
  connections: [READ_ONLY],
  connectionsLoading: false,
  selectedConnectionId: READ_ONLY.id,
  onSelectConnection: () => {},
  parentZones: [{ id: "0123456789abcdef0123456789abcdef", name: "conflict.cloud" }],
  zonesLoading: false,
  zonesError: null,
  onGrant: async () => true,
  onBind: async () => true,
};
const meta: Meta<typeof DnsDelegationPanel> = {
  title: "Screens/Domains/DnsDelegationPanel",
  component: DnsDelegationPanel,
  parameters: { layout: "padded" },
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="en" messages={en} timeZone="UTC">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  args: DELEGATION,
};
export default meta;
type Story = StoryObj<typeof DnsDelegationPanel>;

export const GrantParentZone: Story = {};
export const ReadyToBind: Story = {
  args: { connections: [READ_ONLY, GRANTED], selectedConnectionId: null, parentZones: null },
};
export const BoundAndWritten: Story = {
  args: {
    connections: [READ_ONLY, GRANTED],
    selectedConnectionId: null,
    parentZones: null,
    boundConnectionId: GRANTED.id,
    boundZoneName: "conflict.cloud",
    lastWrite: {
      action: "created",
      nameservers: ["ns-1.awsdns-01.org", "ns-2.awsdns-02.co.uk"],
      at: "2026-10-15T12:00:00Z",
    },
  },
};
export const Conflict: Story = {
  args: {
    ...BoundAndWritten.args,
    lastWrite: {
      action: "conflict",
      reason: "astro.conflict.cloud is delegated to ns.elsewhere.test",
      at: "2026-10-15T12:00:00Z",
    },
  },
};
export const NoParentInAccount: Story = { args: { parentZones: [] } };
export const TokenRefused: Story = { args: { error: "TOKEN_INACTIVE" } };
export const OperatorOnly: Story = {
  args: { canWrite: false, connections: null, selectedConnectionId: null, parentZones: null },
};
/**
 * calliope-installer#447: the install withholds DNS, so provisioning writes no
 * delegation records. The binding and last write still show; grant and bind
 * do not. DnsUnset is the same bound domain without the restriction.
 */
const withheldPlay =
  (withheld: boolean): Story["play"] =>
  async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("ns-1.awsdns-01.org")).toBeVisible();
    if (withheld) {
      await expect(canvas.getByText(/DNS is withheld/)).toBeVisible();
      await expect(canvas.queryByRole("button")).toBeNull();
      await expect(canvas.queryByLabelText("Write token")).toBeNull();
    } else {
      await expect(canvas.queryByText(/DNS is withheld/)).toBeNull();
      await expect(canvas.getByRole("button", { name: "Grant write access" })).toBeVisible();
    }
  };
export const DnsWithheld: Story = {
  args: { ...BoundAndWritten.args, dnsWithheld: true },
  play: withheldPlay(true),
};
export const DnsUnset: Story = {
  args: { ...BoundAndWritten.args, dnsWithheld: false },
  play: withheldPlay(false),
};
