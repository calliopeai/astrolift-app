import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import {
  CI_TOKEN,
  DANA,
  ENG_GROUP,
  LONG_PRINCIPAL,
  PAYMENTS_TEAM,
  PRINCIPALS,
  SAM,
} from "./fixtures";
import { PrincipalChip } from "./PrincipalChip";

/**
 * Who holds access: a user, an IdP group, a team or a token, told apart by
 * icon and noun, with the stable id in mono.
 */
const meta: Meta<typeof PrincipalChip> = {
  title: "Access/PrincipalChip",
  component: PrincipalChip,
  parameters: { layout: "padded" },
  args: { principal: DANA },
};
export default meta;

type Story = StoryObj<typeof PrincipalChip>;

export const User: Story = {};

export const Group: Story = { args: { principal: ENG_GROUP } };

export const Team: Story = { args: { principal: PAYMENTS_TEAM } };

export const Token: Story = { args: { principal: CI_TOKEN } };

/** Every kind, as a list cell: name, id, and the detail line. */
export const Block: Story = {
  render: () => (
    <ul className="flex max-w-md flex-col gap-3">
      {PRINCIPALS.map((p) => (
        <li key={`${p.kind}:${p.id}`}>
          <PrincipalChip principal={p} variant="block" />
        </li>
      ))}
    </ul>
  ),
};

/** Picks in a multi-select, each with its remove button. */
export const Removable: Story = {
  render: () => (
    <div className="flex max-w-md flex-wrap gap-2">
      {[DANA, SAM, ENG_GROUP, PAYMENTS_TEAM].map((p) => (
        <PrincipalChip key={`${p.kind}:${p.id}`} principal={p} onRemove={() => {}} />
      ))}
    </div>
  ),
};

export const WithoutId: Story = { args: { principal: DANA, showId: false } };

export const LongStrings: Story = {
  render: () => (
    <div className="flex max-w-xs flex-col gap-3 border p-2">
      <PrincipalChip principal={LONG_PRINCIPAL} variant="block" />
      <PrincipalChip principal={LONG_PRINCIPAL} onRemove={() => {}} />
    </div>
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="flex flex-wrap gap-3 overflow-hidden border p-4">
      {[...PRINCIPALS, LONG_PRINCIPAL].map((p) => (
        <PrincipalChip key={`${p.kind}:${p.id}`} principal={p} onRemove={() => {}} />
      ))}
    </div>
  ),
};
