import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { useLocalListState } from "@/components/list/use-list-state";
import { MembershipRosterPanel, MembershipReviewPanel } from "./TeamMembershipPanels";

import { row, review } from "./team-membership.fixtures";
const meta: Meta = {
  title: "Screens/Administration/Access/ReviewedTeamMemberships",
  parameters: { layout: "padded" },
};
export default meta;
type Story = StoryObj;
function Roster({
  direction = "team",
  readOnly = false,
}: {
  direction?: "team" | "person";
  readOnly?: boolean;
}) {
  const t = useTranslations("teams.memberships");
  const list = useLocalListState({
    id: "team.membership.story",
    fields: [],
    searchPlaceholder: t("searchPeople"),
    defaultSort: [],
    views: [{ key: "all", label: t("all"), filters: {} }],
    paging: "numbered",
    pageSizes: [25, 50, 100],
  });
  const [selected, setSelected] = useState<"ADD" | "REMOVE" | null>(null);
  const [roleId, setRoleId] = useState("");
  return selected ? (
    <MembershipReviewPanel
      review={
        selected === "ADD"
          ? {
              ...review,
              kind: "ADD",
              roles: [
                {
                  id: row.sources[0].roleId,
                  version: 1,
                  name: "Team reader",
                  permissions: ["team.read"],
                },
              ],
            }
          : review
      }
      phase="review"
      roleId={roleId}
      onRole={setRoleId}
      onConfirm={() => {}}
      onRetryOriginal={() => {}}
      onReviewAgain={() => {}}
      error={null}
    />
  ) : (
    <MembershipRosterPanel
      direction={direction}
      list={list}
      rows={[{ ...row, canRemove: !readOnly }]}
      totalCount={1}
      loading={false}
      error={null}
      onRetry={() => {}}
      canAdd={!readOnly}
      onAdd={() => setSelected("ADD")}
      onRemove={() => setSelected("REMOVE")}
    />
  );
}
export const Team: Story = { render: () => <Roster /> };
export const Person: Story = { render: () => <Roster direction="person" /> };
export const ReadOnly: Story = { render: () => <Roster readOnly /> };
export const RemoveReview: Story = {
  render: () => (
    <MembershipReviewPanel
      review={review}
      phase="review"
      roleId=""
      onRole={() => {}}
      onConfirm={() => {}}
      onRetryOriginal={() => {}}
      onReviewAgain={() => {}}
      error={null}
    />
  ),
};
export const LostReply: Story = {
  render: () => (
    <MembershipReviewPanel
      review={review}
      phase="uncertain"
      roleId=""
      onRole={() => {}}
      onConfirm={() => {}}
      onRetryOriginal={() => {}}
      onReviewAgain={() => {}}
      error={null}
    />
  ),
};
export const CommittedRefreshFailed: Story = {
  render: () => (
    <MembershipReviewPanel
      review={review}
      phase="refreshFailed"
      roleId=""
      onRole={() => {}}
      onConfirm={() => {}}
      onRetryOriginal={() => {}}
      onReviewAgain={() => {}}
      error={null}
    />
  ),
};
