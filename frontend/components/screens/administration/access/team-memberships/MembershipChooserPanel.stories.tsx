import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useTranslations } from "next-intl";
import { useLocalListState } from "@/components/list/use-list-state";
import { MembershipChooserPanel } from "./MembershipChooserPanel";
function Choices() {
  const t = useTranslations("teams.memberships");
  const list = useLocalListState({
    id: "membership-choice-story",
    fields: [],
    searchPlaceholder: t("searchPeople"),
    defaultSort: [],
    views: [{ key: "all", label: t("all"), filters: {} }],
    paging: "numbered",
    pageSizes: [25, 50, 100],
  });
  return (
    <MembershipChooserPanel
      direction="team"
      list={list}
      rows={[
        {
          id: "019e0000-0000-7000-8000-000000000002",
          name: "Example person",
          detail: "person@example.test",
        },
      ]}
      totalCount={1}
      loading={false}
      error={null}
      onRetry={() => {}}
      onChoose={() => {}}
    />
  );
}
const meta = {
  title: "Screens/Administration/Access/MembershipChooser",
  parameters: { layout: "padded" },
} satisfies Meta;
export default meta;
export const People: StoryObj<typeof meta> = { render: () => <Choices /> };
