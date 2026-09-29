import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { DOCS_C_KINDS_EMPTY, DOCS_C_KINDS_FULL, DOCS_C_KINDS_LONG } from "./docs-c.fixtures";
import { IdentityProvidersDoc } from "./IdentityProvidersDoc";

const meta: Meta<typeof IdentityProvidersDoc> = {
  title: "Screens/Documentation/IdentityProvidersDoc",
  component: IdentityProvidersDoc,
  parameters: { layout: "fullscreen" },
};
export default meta;

type Story = StoryObj<typeof IdentityProvidersDoc>;

/** Static copy: there is no loading or error state; Full is the real page. */
export const Full: Story = { args: { kinds: DOCS_C_KINDS_FULL } };

/** No provider kinds listed; the surrounding sections still render. */
export const Empty: Story = { args: { kinds: DOCS_C_KINDS_EMPTY } };

export const LongStrings: Story = { args: { kinds: DOCS_C_KINDS_LONG } };
