import { NextIntlClientProvider, useTranslations } from "next-intl";
import de from "@/messages/de.json";
import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { useLocalListState } from "@/components/list/use-list-state";
import { localizedEntityAccessList } from "@/components/screens/administration/access/entity-access";
import {
  entityAccessProps,
  TEAM_ACCESS,
} from "@/components/screens/administration/access/principal.fixtures";
import type { AccessEntry } from "@/components/screens/administration/access/entity-access";

import { TeamAccessPanel } from "./TeamAccessPanel";
import { PROJECTS } from "./teams.fixtures";

const meta: Meta = {
  title: "Screens/Teams/TeamAccessPanel",
  parameters: { layout: "padded" },
};
export default meta;

type Story = StoryObj;

const LONG_ENTRY: AccessEntry = {
  ...TEAM_ACCESS[2]!,
  bindingId: "rb-long",
  groupExternalId: "azure_ad:emea-regional-compliance-and-release-coordination-group-0001",
  sourceScopeLabel:
    "project emea/emea-subsidiary-quarter-end-freeze-coordination-and-release-readiness",
};

function Panel({
  entries = TEAM_ACCESS,
  projects = PROJECTS,
  loading = false,
}: {
  entries?: AccessEntry[];
  projects?: typeof PROJECTS;
  loading?: boolean;
}) {
  const t = useTranslations("shared.access.entityPanel"),
    teamT = useTranslations("teams.access");
  const list = useLocalListState(localizedEntityAccessList(t));
  return (
    <TeamAccessPanel
      slug="platform"
      access={{
        list,
        ...entityAccessProps({
          rows: entries,
          loading,
          subject: teamT("subject", { name: "platform" }),
        }),
      }}
      reach={{ projects, loading, error: null, onRetry: () => {} }}
    />
  );
}

/** Who has access on the team, and why, beside what a grant here reaches. */
export const Full: Story = { render: () => <Panel /> };

export const Loading: Story = { render: () => <Panel entries={[]} projects={[]} loading /> };

export const Empty: Story = { render: () => <Panel entries={[]} projects={[]} /> };

export const LongStrings: Story = {
  render: () => <Panel entries={[LONG_ENTRY, ...TEAM_ACCESS]} />,
};

export const Width768: Story = {
  render: () => (
    <div style={{ width: 768 }} className="overflow-hidden border p-4">
      <Panel entries={[LONG_ENTRY, ...TEAM_ACCESS]} />
    </div>
  ),
};

export const GermanWidth768: Story = {
  decorators: [
    (Story) => (
      <NextIntlClientProvider locale="de" messages={de} timeZone="Europe/Berlin">
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
function ReachFailed() {
  const t = useTranslations("shared.access.entityPanel"),
    list = useLocalListState(localizedEntityAccessList(t));
  return (
    <TeamAccessPanel
      slug="platform"
      access={{ list, ...entityAccessProps() }}
      reach={{
        projects: [],
        loading: false,
        error: { message: "RAW_PROJECT_READ_FAILURE" },
        onRetry: () => {},
      }}
    />
  );
}
export const ReachReadFailed: Story = { render: () => <ReachFailed /> };
