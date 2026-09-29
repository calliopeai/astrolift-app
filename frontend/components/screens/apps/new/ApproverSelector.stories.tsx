import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";

import {
  APPROVER_DATA,
  APPROVER_EMPTY,
  APPROVER_ERROR,
  APPROVER_LOADING,
  APPROVER_LONG,
  NO_SELECTION,
  TEAM_SELECTION,
  USERS_SELECTION,
} from "./apps-wizard-steps-a.fixtures";
import { type ApproverSelection, ApproverSelectorView } from "./ApproverSelector";
import type { ApproverSelectorData } from "./use-approver-selector";

const meta: Meta = { title: "Screens/Apps/New/ApproverSelector" };
export default meta;

type Story = StoryObj;

/** Holds the selection so picking, removing, and the XOR toggle work. */
function Demo({ data, initial }: { data: ApproverSelectorData; initial: ApproverSelection }) {
  const [value, setValue] = React.useState(initial);
  return (
    <ApproverSelectorView {...data} value={value} onChange={setValue} onValidityChange={() => {}} />
  );
}

export const Loading: Story = {
  render: () => <Demo data={APPROVER_LOADING} initial={NO_SELECTION} />,
};

/** The org has no approver-eligible members yet. */
export const Empty: Story = {
  render: () => <Demo data={APPROVER_EMPTY} initial={NO_SELECTION} />,
};

export const LoadFailed: Story = {
  render: () => <Demo data={APPROVER_ERROR} initial={NO_SELECTION} />,
};

/** Nothing picked: the "pick a team or users" validity message shows. */
export const NothingPicked: Story = {
  render: () => <Demo data={APPROVER_DATA} initial={NO_SELECTION} />,
};

/** Two users picked, plus one id no longer in the picker (flagged orphan). */
export const Full: Story = {
  render: () => <Demo data={APPROVER_DATA} initial={USERS_SELECTION} />,
};

/** A team picked: the user list is hidden (team XOR users). */
export const TeamPicked: Story = {
  render: () => <Demo data={APPROVER_DATA} initial={TEAM_SELECTION} />,
};

export const LongStrings: Story = {
  render: () => (
    <Demo
      data={APPROVER_LONG}
      initial={{ approverUserIds: ["u-long"], approverTeamId: "", minimumApprovals: 1 }}
    />
  ),
};
