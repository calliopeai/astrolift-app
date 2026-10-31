import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { PERSON, PERSON_UNRESOLVED } from "./fixtures";
import { PersonLoginsNotice } from "./PersonLoginsNotice";

/** The person in each cluster's identity provider, above their list (#2534). */
const meta: Meta<typeof PersonLoginsNotice> = {
  title: "Screens/Administration/AppAccess/PersonLoginsNotice",
  component: PersonLoginsNotice,
};
export default meta;

type Story = StoryObj<typeof PersonLoginsNotice>;

/** A login with groups; four environments refuse them. */
export const Resolved: Story = { args: { person: PERSON } };

/** One provider cannot be listed, and the other has no login for them. */
export const Unresolved: Story = { args: { person: PERSON_UNRESOLVED } };

/** Disabled in the provider: they enter nothing. */
export const Disabled: Story = {
  args: {
    person: {
      ...PERSON,
      logins: [{ ...PERSON.logins[0]!, enabled: false }],
      refusedCount: 9,
    },
  },
};

/** None of the apps in view is on an Envoy edge. */
export const NoEdge: Story = { args: { person: { ...PERSON, logins: [], refusedCount: 0 } } };
