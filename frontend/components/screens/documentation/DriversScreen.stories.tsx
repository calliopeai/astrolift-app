import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import {
  DOCS_E_DRIVERS,
  DOCS_E_DRIVERS_FALLBACK,
  DOCS_E_DRIVERS_LOADING,
  DOCS_E_DRIVERS_LONG,
} from "./docs-e.fixtures";
import { DriversScreen } from "./DriversScreen";

const meta: Meta<typeof DriversScreen> = {
  title: "Screens/Documentation/DriversScreen",
  component: DriversScreen,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof DriversScreen>;

export const Loading: Story = { args: DOCS_E_DRIVERS_LOADING };

export const Full: Story = { args: DOCS_E_DRIVERS };

/** No plugins registered: the static known-provider list with the fallback notice. */
export const Empty: Story = { args: DOCS_E_DRIVERS_FALLBACK };

export const QueryFailed: Story = {
  args: {
    ...DOCS_E_DRIVERS_FALLBACK,
    rows: [],
    usingFallback: false,
    error: { name: "Error", message: "Permission denied while loading this section" },
  },
};

export const LongStrings: Story = { args: DOCS_E_DRIVERS_LONG };

/** The same providers read as the managed-services matrix. */
export const ManagedServices: Story = {
  args: DOCS_E_DRIVERS,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("tab", { name: "Managed services per provider" }));
    await expect(c.getByRole("tab", { name: "Managed services per provider" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await expect(c.getByRole("columnheader", { name: "postgres" })).toBeInTheDocument();
  },
};
