import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import * as React from "react";
import { expect, userEvent, within } from "storybook/test";

import { type ListState, useLocalListState } from "@/components/list/use-list-state";

import {
  IMPORT_SKILLS,
  LONG_SKILL,
  MANY_SKILLS,
  serveSkills,
  SKILL_ITEMS,
} from "./agent-skills.fixtures";
import { ImportSkillsSheet } from "./ImportSkillsSheet";
import { SKILLS_LIST, type SkillListItem } from "./skills-list";
import { SkillsListScreen, type SkillsListScreenProps } from "./SkillsListScreen";

const meta: Meta = {
  title: "Screens/Agents/Skills/SkillsListScreen",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

type Props = Partial<Omit<SkillsListScreenProps, "list">> & {
  skills?: SkillListItem[];
  initial?: Partial<ListState>;
  importOpen?: boolean;
};

/** The screen over fixture skills, filtered and paged the way the server does it. */
function Skills({ skills = SKILL_ITEMS, initial, importOpen = false, ...patch }: Props) {
  const list = useLocalListState(SKILLS_LIST, initial);
  const [open, setOpen] = React.useState(importOpen);
  const { rows, totalCount } = serveSkills(skills, {
    filters: list.filters,
    q: list.state.q,
    sort: list.state.sort,
    page: list.state.page,
    pageSize: list.state.pageSize,
  });
  return (
    <SkillsListScreen
      list={list}
      rows={rows}
      totalCount={totalCount}
      loading={false}
      error={null}
      onRetry={() => {}}
      onImportOpenChange={setOpen}
      importSheet={<ImportSkillsSheet {...IMPORT_SKILLS} open={open} onOpenChange={setOpen} />}
      {...patch}
    />
  );
}

/** Newest first: own skills, an inactive one with no description, a global one. */
export const Full: Story = { render: () => <Skills /> };

export const Loading: Story = { render: () => <Skills skills={[]} loading /> };

/** No skills yet: New skill. */
export const Empty: Story = { render: () => <Skills skills={[]} /> };

export const LoadError: Story = {
  render: () => (
    <Skills skills={[]} error={{ message: "Response not successful: Received status code 500" }} />
  ),
};

/** Mine: the skills the viewer wrote or imported. */
export const Mine: Story = { render: () => <Skills initial={{ view: "mine" }} /> };

/** Imported: the skills that came from a repo or the catalogue. */
export const Imported: Story = { render: () => <Skills initial={{ view: "imported" }} /> };

/** A chip that matches nothing: "No skills match" and Clear. */
export const EmptyFiltered: Story = {
  render: () => <Skills initial={{ filters: { status: "inactive", scope: "global" } }} />,
};

/** Sixty skills: numbered pages, page 2 of 3. */
export const Paged: Story = { render: () => <Skills skills={MANY_SKILLS} initial={{ page: 2 }} /> };

/** A 64-char SHA slug, a 200-char ARN and an unbroken URL in the description. */
export const LongStrings: Story = {
  render: () => <Skills skills={[LONG_SKILL, ...SKILL_ITEMS]} />,
};

/** The narrowest supported width: the table scrolls in its own frame, the page does not. */
export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }}>
      <Skills skills={[LONG_SKILL, ...MANY_SKILLS]} />
    </div>
  ),
};

/** Import from repo, a two-field sheet opened from `⋯`. */
export const ImportOpen: Story = { render: () => <Skills importOpen /> };

/** Views are the header's tabs, New skill links to its page, and Import sits in `⋯`. */
export const HeaderNavigation: Story = {
  render: () => <Skills />,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    for (const view of ["All", "Mine", "Imported"]) {
      await expect(canvas.getByRole("link", { name: view })).toBeInTheDocument();
    }
    await expect(canvas.getByRole("link", { name: "New skill" })).toHaveAttribute(
      "href",
      "/agents/skills/new"
    );
    await userEvent.click(canvas.getByRole("button", { name: "Agents: switch" }));
    await expect(
      await within(document.body).findByRole("menuitem", { name: /Tools/ })
    ).toBeInTheDocument();
  },
};
