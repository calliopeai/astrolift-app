import { NextIntlClientProvider, useTranslations } from "next-intl";
import ja from "@/messages/ja.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { expect, userEvent, within } from "storybook/test";

import { useLocalListState } from "@/components/list/use-list-state";

import { localizedEntityAccessList } from "./entity-access";
import { EntityAccessPanel, type EntityAccessPanelProps } from "./EntityAccessPanel";
import { APP_ACCESS, entityAccessProps, TEAM_ACCESS } from "./principal.fixtures";

const meta: Meta = {
  title: "Screens/Administration/Access/EntityAccessPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

function Panel(overrides: Partial<Omit<EntityAccessPanelProps, "list">>) {
  const t = useTranslations("shared.access.entityPanel");
  const list = useLocalListState(localizedEntityAccessList(t));
  return <EntityAccessPanel list={list} {...entityAccessProps(overrides)} />;
}

/** A team: a direct grant, an org grant reaching it, a group grant and a group mapping. */
export const Team: Story = { render: () => <Panel /> };

/** An app: a team share reads as via the team, changed on the app's sharing. */
export const App: Story = {
  render: () => <Panel subject="app checkout-api" rows={APP_ACCESS} />,
};

/** Removing an org grant from a team's tab says it goes at the org. */
export const RemoveInherited: Story = {
  render: () => <Panel rows={TEAM_ACCESS.slice(1, 2)} />,
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /row actions|actions/i }));
    await userEvent.click(await within(document.body).findByRole("menuitem", { name: /Remove/ }));
    await expect(await within(document.body).findByRole("alertdialog")).toHaveTextContent(
      "not on team platform"
    );
  },
};

export const Loading: Story = { render: () => <Panel rows={[]} loading /> };

export const Empty: Story = { render: () => <Panel rows={[]} /> };

export const LoadFailed: Story = {
  render: () => <Panel rows={[]} error={{ message: "upstream timed out after 30s" }} />,
};

export const ReadOnly: Story = { render: () => <Panel canManage={false} /> };

export const LongStrings: Story = {
  render: () => (
    <Panel
      rows={[
        {
          ...TEAM_ACCESS[2]!,
          bindingId: "rb-long",
          groupExternalId: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
          sourceScopeLabel:
            "project emea/emea-subsidiary-quarter-end-freeze-coordination-and-release-readiness",
          inherited: true,
        },
        ...TEAM_ACCESS,
      ]}
    />
  ),
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel rows={APP_ACCESS} />
    </div>
  ),
};

export const JapaneseWidth768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="ja" messages={ja} timeZone="Asia/Tokyo">
        <Story />
      </NextIntlClientProvider>
    ),
  ],
  render: () => (
    <div style={{ width: 768 }}>
      <Panel />
    </div>
  ),
};
export const RemoveRefused: Story = {
  render: () => (
    <Panel
      rows={TEAM_ACCESS.slice(0, 1)}
      onRemove={async () => {
        throw new Error("RAW_REMOVE_REFUSAL");
      }}
    />
  ),
  play: async ({ canvasElement }) => {
    const c = within(canvasElement);
    await userEvent.click(c.getByRole("button", { name: /row actions|actions/i }));
    await userEvent.click(
      await within(document.body).findByRole("menuitem", { name: "Remove this grant" })
    );
  },
};
