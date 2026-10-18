import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, fn, userEvent, within } from "storybook/test";

import { ExpiryBadge } from "./ExpiryBadge";

const NOW = Date.parse("2026-10-17T12:00:00Z");
const at = (ms: number) => new Date(NOW + ms).toISOString();
const HOUR = 3_600_000;

const meta: Meta<typeof ExpiryBadge> = {
  title: "Screens/Apps/ExpiryBadge",
  component: ExpiryBadge,
  args: { now: NOW, expiresAt: at(76 * HOUR) },
};
export default meta;

type Story = StoryObj<typeof ExpiryBadge>;

/** Days left: a quiet outline badge. */
export const Days: Story = {
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByText("Expires in 3d 4h")).toBeInTheDocument();
  },
};

/** Inside the last day (the 24 h warning has gone out): amber. */
export const UnderADay: Story = { args: { expiresAt: at(5 * HOUR + 12 * 60_000) } };

/** Inside the last hour: red. */
export const UnderAnHour: Story = { args: { expiresAt: at(42 * 60_000) } };

/** Past its expiry, while the teardown runs: no extend. */
export const Expired: Story = {
  args: { expiresAt: at(-60_000), onExtend: fn() },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await expect(canvas.getByText("Expired")).toBeInTheDocument();
    await expect(canvas.queryByRole("button")).toBeNull();
  },
};

/** With `app.update`: one click extends by a day. */
export const WithExtend: Story = {
  args: { expiresAt: at(20 * HOUR), onExtend: fn() },
  play: async ({ args, canvasElement }) => {
    await userEvent.click(within(canvasElement).getByRole("button", { name: "Extend by one day" }));
    await expect(args.onExtend).toHaveBeenCalledOnce();
  },
};

/** While the extend runs. */
export const Extending: Story = {
  args: { expiresAt: at(20 * HOUR), onExtend: fn(), extending: true },
};

/** A permanent app renders nothing. */
export const Permanent: Story = {
  args: { expiresAt: null },
  render: (args) => (
    <div data-testid="expiry-empty">
      <ExpiryBadge {...args} />
    </div>
  ),
  play: async ({ canvasElement }) => {
    await expect(within(canvasElement).getByTestId("expiry-empty")).toBeEmptyDOMElement();
  },
};
